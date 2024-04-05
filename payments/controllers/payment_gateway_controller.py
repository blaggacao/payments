import json

from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.integrations.utils import create_request_log
from frappe.utils import get_url

from payments.utils import TX_REFERENCE_KEY

from types import MappingProxyType

from payments.types import (
	Initiated,
	TxData,
	Processed,
	ILogName,
	PaymentUrl,
	PaymentMandate,
	FlowType,
	Proceeded,
	RemoteServerInitiationPayload,
	RemoteServerProcessingPayload,
)

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
	from payments.payments.doctype.payment_gateway_integration_log.payment_gateway_integration_log import (
		PaymentGatewayIntegrationLog,
	)
	from payments.payments.doctype.payment_gateway.payment_gateway import PaymentGateway


def _error_value(error, flow):
	return _(
		"Our server had an issue processing your {0}. Please contact customer support mentioning: {1}"
	).format(flow, error)


def _help_me_develop(state):
	from pprint import pprint

	print("self.state: ")
	pprint(state)


class PaymentGatewayController(Document):
	"""This controller implemets the public API of payment gateway controllers."""

	def __init__(self, *args, **kwargs):
		super(Document, self).__init__(*args, **kwargs)
		self.state = frappe._dict()

	@staticmethod
	def initiate(
		payment_gateway_name: str, tx_data: TxData, correlation_id: str | None, name: str | None
	) -> ILogName:
		"""Initiate a payment flow from Ref Doc with the given gateway.

		Inheriting methods can invoke super and then set e.g. request_id on self.state.ilog to save
		and early-obtained correlation id from the payment gateway or to initiate the user flow if delegated to
		the controller (see: is_user_flow_initiation_delegated)
		"""
		gateway: PaymentGateway = frappe.get_cached_doc("Payment Gateway", payment_gateway_name)

		if not gateway.gateway_controller and not gateway.gateway_settings:
			frappe.throw(
				_(
					"{0} is not fully configured, both Gateway Settings and Gateway Controller need to be set"
				).format(payment_gateway_name)
			)

		self = frappe.get_cached_doc(
			gateway.gateway_settings,
			gateway.gateway_controller or gateway.gateway_settings,  # may be a singleton
		)

		tx_data = self._patch_tx_data(tx_data)  # controller specific modifications

		# service_name is used here as short-cut to recover the controller from the tx reference (i.e. interation request name) from
		# the front end without the need for going over the reference document which may be hinreder by permissions or add latency
		self.state.ilog = create_request_log(
			tx_data, service_name=f"{self.doctype}[{self.name}]", name=name
		)
		return self.state.ilog.name

	@staticmethod
	def get_payment_url(ilog_name: ILogName) -> PaymentUrl | None:
		"""Use the payment url to initiate the user flow, for example via email or chat message.

		Beware, that the controller might not implement this and in that case return: None
		"""
		query_param = urlencode({TX_REFERENCE_KEY: ilog_name})
		return get_url(f"./pay?{query_param}")

	@staticmethod
	def proceed(ilog_name: ILogName, updated_tx_data: TxData | None) -> Proceeded:
		"""Call this when the user agreed to proceed with the payment to initiate the capture with
		the remote payment gateway.

		If the capture is initialized by the gatway, call this immediatly with out waiting for the
		user OK signal.

		updated_tx_data:
		   Pass any update to the inital transaction data; this can reflect later customer choices
		   and thereby modify the flow

		Example:
		```python
		if controller.is_user_flow_initiation_delegated():
		        controller.proceed()
		else:
		        # example (depending on the doctype & business flow):
		        # 1. send email with payment link
		        # 2. let user open the link
		        # 3. upon rendering of the page: call proceed; potentially with tx updates
		        pass
		```
		"""
		ilog: PaymentGatewayIntegrationLog = frappe.get_doc("Payment Gateway Integration Log", ilog_name)
		ilog.update_status(updated_tx_data or {}, "Queued")

		self = frappe.get_doc(ilog.url)

		self.state.ilog = MappingProxyType(ilog.as_dict())
		self.state.tx_data = MappingProxyType(json.loads(ilog.data))
		self.state.mandate: PaymentMandate = self._get_mandate()

		try:

			if self._should_have_mandate() and not self.mandate:
				self.state.mandate = self._create_mandate()
				initiated = self._initiate_mandate_acquisition()
				ilog.db_set("flow_type", FlowType.mandate_acquisition)
				ilog.db_set("request_id", initiated.correlation_id, commit=True)
				ilog.update_status({"saved_mandate": self.state.mandate.name}, ilog.status)
				return Proceeded(
					gateway=self.name,
					type=FlowType.mandate_acquisition,
					mandate=self.state.mandate,
					txdata=self.state.tx_data,
					payload=initiated.payload,
				)
			elif self.state.mandate:
				initiated = self._initiate_mandated_charge()
				ilog.db_set("flow_type", FlowType.mandated_charge)
				ilog.db_set("request_id", initiated.correlation_id, commit=True)
				ilog.update_status({"saved_mandate": self.state.mandate.name}, ilog.status)
				return Proceeded(
					gateway=self.name,
					type=FlowType.mandated_charge,
					mandate=self.state.mandate,
					txdata=self.state.tx_data,
					payload=initiated.payload,
				)
			else:
				initiated = self._initiate_charge()
				ilog.db_set("flow_type", FlowType.charge)
				ilog.db_set("request_id", initiated.correlation_id, commit=True)
				return Proceeded(
					gateway=self.name,
					type=FlowType.charge,
					txdata=self.state.tx_data,
					payload=initiated.payload,
				)

		except Exception as e:
			error = ilog.log_error(title="Initiated Failure")
			frappe.redirect_to_message(
				_("Payment Gateway Error"),
				_(
					"There has been an issue with the server's configuration for {0}. Please contact customer care mentioning: {1}"
				).format(self.name, error),
				http_status_code=401,
				indicator_color="yellow",
			)

	@staticmethod
	def process_response(ilog_name: ILogName, payload: RemoteServerProcessingPayload) -> Processed:
		"""Call this from the controlling business logic; either backend or frontens.

		It will recover the correct controller and dispatch the correct processing based on data that is at this
		point already stored in the integration log

		payload:
		    this is a signed, sensitive response containing the payment status; the signature is validated prior
		    to processing by controller._validate_response_payload
		"""

		ilog: PaymentGatewayIntegrationLog = frappe.get_cached_doc(
			"Payment Gateway Integration Log", ilog_name
		)
		self: "PaymentGatewayController" = frappe.get_cached_doc(ilog.url)

		assert (
			self.success_states
		), "the controller must declare its `.success_states` as an iterable on the class"

		self.state.ilog = MappingProxyType(ilog.as_dict())
		self.state.tx_data = MappingProxyType(json.loads(ilog.data))  # QoL
		self.state.response_payload = MappingProxyType(payload)
		# guard against already processed or currently being processed payloads via another entrypoint
		try:
			ilog.lock(timeout=3)  # max processing allowance of alternative flow
		except frappe.DocumentLockedError:
			return self.state.tx_data.get("saved_return_value")

		try:
			self._validate_response_payload()
		except Exception:
			error = ilog.log_error("Response validation failure")
			frappe.redirect_to_message(
				_("Server Error"),
				_("There's been an issue with your payment."),
				http_status_code=500,
				indicator_color="red",
			)

		ref_doc = frappe.get_doc(ilog.reference_doctype, ilog.reference_docname)

		def _process_response(callable, hookmethod, flowtype) -> Processed:
			processed = None
			try:
				processed = callable()
			except Exception:
				error = ilog.log_error(f"Processing failure ({flowtype})")
				ilog.handle_failure(self.state.response_payload)
				frappe.redirect_to_message(
					_("Server Error"),
					_error_value(error, flowtype),
					http_status_code=500,
					indicator_color="red",
				)

			assert (
				self.flags.status_changed_to
			), f"_process_response_for_{flowtype} must set self.flags.status_changed_to"

			try:
				if ref_doc.hasattr(hookmethod):
					if res := ref_doc.run_method(hookmethod, MappingProxyType(self.flags), self.state):
						processed = Processed(gateway=self.name, **res)
			except Exception:
				error = ilog.log_error(f"Processing failure ({flowtype} - refdoc hook)")
				frappe.redirect_to_message(
					_("Server Error"),
					_error_value(error, f"{flowtype} (via ref doc hook)"),
					http_status_code=500,
					indicator_color="red",
				)

			if self.flags.status_changed_to in self.success_states:
				ilog.handle_success(self.state.response_payload)
				processed = processed or Processed(
					gateway=self.name,
					message=_("Payment {} succeeded").format(flowtype),
					action={"redirect_to": "/"},
					payload=None,
				)
			elif self.pre_authorized_states and self.flags.status_changed_to in self.pre_authorized_states:
				ilog.handle_success(self.state.response_payload)
				ilog.db_set("status", "Authorized", update_modified=False)
				processed = processed or Processed(
					gateway=self.name,
					message=_("Payment {} authorized").format(flowtype),
					action={"redirect_to": "/"},
					payload=None,
				)
			elif self.waiting_states and self.flags.status_changed_to in self.waiting_states:
				ilog.handle_success(self.state.response_payload)
				ilog.db_set("status", "Waiting", update_modified=False)
				processed = processed or Processed(
					gateway=self.name,
					message=_("Payment {} awaiting further processing by the bank").format(flowtype),
					action={"redirect_to": "/"},
					payload=None,
				)
			else:
				ilog.handle_failure(self.state.response_payload)
				try:
					if ref_doc.hasattr("on_payment_failed"):
						msg = self._render_failure_message()
						status = self.flags.status_changed_to
						ref_doc.run_method("on_payment_failed", status, msg)
				except Exception:
					error = ilog.log_error("Setting failure message on ref doc failed")
				processed = processed or Processed(
					gateway=self.name,
					message=_("Payment {} failed").format(flowtype),
					action={"redirect_to": "/"},
					payload=None,
				)

			return processed

		match ilog.flow_type:
			case FlowType.mandate_acquisition:
				self.state.mandate: PaymentMandate = self._get_mandate()
				processed: Processed = _process_response(
					callable=self._process_response_for_mandate_acquisition,
					hookmethod="on_payment_mandate_acquisition_processed",
					flowtype="mandate adquisition",
				)
			case FlowType.mandated_charge:
				self.state.mandate: PaymentMandate = self._get_mandate()
				processed: Processed = _process_response(
					callable=self._process_response_for_mandated_charge,
					hookmethod="on_payment_mandated_charge_processed",
					flowtype="mandated charge",
				)
			case FlowType.charge:
				processed: Processed = _process_response(
					callable=self._process_response_for_charge,
					hookmethod="on_payment_charge_processed",
					flowtype="charge",
				)

		ilog.update_status({"saved_return_value": processed.__dict__}, ilog.status)
		return processed

		# ---------------------------------------

	# Lifecycle hooks (contracts)
	#  - imeplement them for your controller
	# ---------------------------------------

	def validate_tx_data(self, tx_data: TxData) -> None:
		"""Invoked by the reference document for example in order to validate the transaction data.

		Should throw on error with an informative user facing message.
		"""
		raise NotImplementedError

	def is_user_flow_initiation_delegated(self, ilog_name: ILogName) -> bool:
		"""If true, you should initiate the user flow from the Ref Doc.

		For example, by sending an email (with a payment url), letting the user make a phone call or initiating a factoring process.

		If false, the gateway initiates the user flow.
		"""
		return False

		# ---------------------------------------

	# Concrete controller methods
	#  - imeplement them for your gateway
	# ---------------------------------------

	def _patch_tx_data(self, tx_data: TxData) -> TxData:
		"""Optional: Implement tx_data preprocessing if required by the gateway.
		For example in order to fix rounding or decimal accuracy.
		"""
		return tx_data

	def _should_have_mandate(self) -> bool:
		"""Optional: Define here, if the TxData store in self.state.tx_data should have a mandate.

		If yes, and the controller hasn't yet found one from a call to self._get_mandate(),
		it will initiate the adquisition of a new mandate in self._create_mandate().

		You have read (!) access to:
		- self.state.ilog
		- self.state.tx_data
		"""
		assert self.state.ilog
		assert self.state.tx_data
		_help_me_develop(self.state)
		return False

	def _get_mandate(self) -> PaymentMandate:
		"""Optional: Define here, how to fetch this controller's mandate doctype instance.

		Since a mandate might be highly controller specific, this is its accessor.

		You have read (!) access to:
		- self.state.ilog
		- self.state.tx_data
		"""
		assert self.state.ilog
		assert self.state.tx_data
		_help_me_develop(self.state)
		return None

	def _create_mandate(self) -> PaymentMandate:
		"""Optional: Define here, how to create controller's mandate doctype instance.

		Since a mandate might be highly controller specific, this is its constructor.

		You have read (!) access to:
		- self.state.ilog
		- self.state.tx_data
		"""
		assert self.state.ilog
		assert self.state.tx_data
		_help_me_develop(self.state)
		return None

	def _initiate_mandate_acquisition(self) -> Initiated:
		"""Invoked by proceed to initiate a mandate acquisiton flow.

		Implementations can read:
		- self.state.ilog
		- self.state.tx_data

		Implementations can read/write:
		- self.state.mandate
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _initiate_mandated_charge(self) -> Initiated:
		"""Invoked by proceed or after having aquired a mandate in order to initiate a mandated charge flow.

		Implementations can read:
		- self.state.ilog
		- self.state.tx_data

		Implementations can read/write:
		- self.state.mandate
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _initiate_charge(self) -> Initiated:
		"""Invoked by proceed in order to initiate a charge flow.

		Implementations can read:
		- self.state.ilog
		- self.state.tx_data
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _validate_response_payload(self) -> None:
		"""Implement how the validation of the response signature

		Implementations can read:
		- self.state.ilog
		- self.state.tx_data
		- self.state.response_payload
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _process_response_for_mandate_acquisition(self) -> Processed | None:
		"""Implement how the controller should process mandate acquisition responses

		Implementations can read:
		- self.state.ilog
		- self.state.tx_data
		- self.state.response_payload

		Implementations can read/write:
		- self.state.mandate
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _process_response_for_mandated_charge(self) -> Processed | None:
		"""Implement how the controller should process mandated charge responses

		Implementations can read:
		- self.state.ilog
		- self.state.tx_data
		- self.state.response_payload

		Implementations can read/write:
		- self.state.mandate
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _process_response_for_charge(self) -> Processed | None:
		"""Implement how the controller should process charge responses

		Implementations can read:
		- self.state.ilog
		- self.state.tx_data
		- self.state.response_payload
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _render_failure_message(self) -> str:
		"""Extract a readable failure message out of the server response

		Implementations can read:
		- self.state.ilog
		- self.state.tx_data
		- self.state.response_payload
		- self.state.mandate; if mandate is involved
		"""
		_help_me_develop(self.state)
		raise NotImplementedError
