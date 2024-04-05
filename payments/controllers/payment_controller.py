import json

from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.model.document import Document
from payments.payments.doctype.payment_session_log.payment_session_log import (
	create_log,
)
from frappe.utils import get_url

from payments.utils import TX_REFERENCE_KEY, recover_references

from types import MappingProxyType

from payments.types import (
	Initiated,
	TxData,
	Processed,
	PSLName,
	PaymentUrl,
	PaymentMandate,
	PSLType,
	Proceeded,
	RemoteServerInitiationPayload,
	RemoteServerProcessingPayload,
	PSLStates,
)

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
	from payments.payments.doctype.payment_session_log.payment_session_log import (
		PaymentSessionLog,
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


class PaymentController(Document):
	"""This controller implemets the public API of payment gateway controllers."""

	def __new__(cls, *args, **kwargs):
		assert cls.flowstates and isinstance(
			cls.flowstates, PSLStates
		), """the controller must declare its flow states in `cls.flowstates`
		and it must be an instance of payments.types.FlowStates
		"""
		return super().__new__(cls, *args, **kwargs)

	def __init__(self, *args, **kwargs):
		super(Document, self).__init__(*args, **kwargs)
		self.state = frappe._dict()

	def load_psl(psl_name: PSLName) -> None:
		pass

	@staticmethod
	def initiate(
		payment_gateway_name: str, tx_data: TxData, correlation_id: str | None, name: str | None
	) -> PSLName:
		"""Initiate a payment flow from Ref Doc with the given gateway.

		Inheriting methods can invoke super and then set e.g. correlation_id on self.state.psl to save
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

		psl = create_log(
			# gateway=f"{self.doctype}[{self.name}]",
			tx_data=tx_data,
			status="Initiated",
		)
		return psl.name

	@staticmethod
	def get_payment_url(psl_name: PSLName) -> PaymentUrl | None:
		"""Use the payment url to initiate the user flow, for example via email or chat message.

		Beware, that the controller might not implement this and in that case return: None
		"""
		query_param = urlencode({TX_REFERENCE_KEY: psl_name})
		return get_url(f"./pay?{query_param}")

	@staticmethod
	def proceed(psl_name: PSLName, updated_tx_data: TxData | None) -> Proceeded:
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

		psl: PaymentSessionLog
		self: "PaymentController"
		psl, self = recover_references(psl_name)

		psl.update_tx_data(updated_tx_data or {}, "Queued")  # commits

		# tx_data = self._patch_tx_data(tx_data)  # controller specific modifications

		self.state = psl.load_state()
		self.state.mandate: PaymentMandate = self._get_mandate()

		try:

			if self._should_have_mandate() and not self.mandate:
				self.state.mandate = self._create_mandate()
				initiated = self._initiate_mandate_acquisition()
				psl.db_set(
					{
						"flow_type": PSLType.mandate_acquisition,
						"correlation_id": initiated.correlation_id,
						"mandate": f"{self.state.mandate.doctype}[{self.state.mandate.name}]",
					},
					commit=True,
				)
				return Proceeded(
					integration=self.doctype,
					psltype=PSLType.mandate_acquisition,
					mandate=self.state.mandate,
					txdata=self.state.tx_data,
					payload=initiated.payload,
				)
			elif self.state.mandate:
				initiated = self._initiate_mandated_charge()
				psl.db_set(
					{
						"flow_type": PSLType.mandated_charge,
						"correlation_id": initiated.correlation_id,
						"mandate": f"{self.state.mandate.doctype}[{self.state.mandate.name}]",
					},
					commit=True,
				)
				return Proceeded(
					integration=self.doctype,
					psltype=PSLType.mandated_charge,
					mandate=self.state.mandate,
					txdata=self.state.tx_data,
					payload=initiated.payload,
				)
			else:
				initiated = self._initiate_charge()
				psl.db_set(
					{
						"flow_type": PSLType.charge,
						"correlation_id": initiated.correlation_id,
					},
					commit=True,
				)
				return Proceeded(
					integration=self.doctype,
					psltype=PSLType.charge,
					txdata=self.state.tx_data,
					payload=initiated.payload,
				)

		except Exception as e:
			error = psl.log_error(title="Initiated Failure")
			frappe.redirect_to_message(
				_("Payment Gateway Error"),
				_(
					"There has been an issue with the server's configuration for {0}. Please contact customer care mentioning: {1}"
				).format(self.name, error),
				http_status_code=401,
				indicator_color="yellow",
			)
			raise frappe.Redirect

	@staticmethod
	def process_response(psl_name: PSLName, payload: RemoteServerProcessingPayload) -> Processed:
		"""Call this from the controlling business logic; either backend or frontens.

		It will recover the correct controller and dispatch the correct processing based on data that is at this
		point already stored in the integration log

		payload:
		    this is a signed, sensitive response containing the payment status; the signature is validated prior
		    to processing by controller._validate_response_payload
		"""

		psl: PaymentSessionLog
		self: "PaymentController"
		psl, self = recover_references(psl_name)

		self.state = psl.load_state()
		self.state.response_payload = MappingProxyType(payload)
		# guard against already processed or currently being processed payloads via another entrypoint
		try:
			psl.lock(timeout=3)  # max processing allowance of alternative flow
		except frappe.DocumentLockedError:
			return self.state.tx_data.get("saved_return_value")

		try:
			self._validate_response_payload()
		except Exception:
			error = psl.log_error("Response validation failure")
			frappe.redirect_to_message(
				_("Server Error"),
				_("There's been an issue with your payment."),
				http_status_code=500,
				indicator_color="red",
			)

		ref_doc = frappe.get_doc(psl.reference_doctype, psl.reference_docname)

		def _process_response(callable, hookmethod, psltype) -> Processed:
			processed = None
			try:
				processed = callable()
			except Exception:
				error = psl.log_error(f"Processing failure ({psltype})")
				psl.handle_failure(self.state.response_payload)
				frappe.redirect_to_message(
					_("Server Error"),
					_error_value(error, psltype),
					http_status_code=500,
					indicator_color="red",
				)

			assert (
				self.flags.status_changed_to
			), f"_process_response_for_{psltype} must set self.flags.status_changed_to"

			try:
				if ref_doc.hasattr(hookmethod):
					if res := ref_doc.run_method(hookmethod, MappingProxyType(self.flags), self.state):
						processed = Processed(gateway=self.name, **res)
			except Exception:
				error = psl.log_error(f"Processing failure ({psltype} - refdoc hook)")
				frappe.redirect_to_message(
					_("Server Error"),
					_error_value(error, f"{psltype} (via ref doc hook)"),
					http_status_code=500,
					indicator_color="red",
				)

			_state_list = (
				self.flowstates.success
				+ self.flowstates.pre_authorized
				+ self.flowstates.processing
				+ self.flowstates.failure
			)

			assert (
				self.flags.status_changed_to in _state_list
			), """
			self.flags.status_changed_to must be in the set of possible states for this controller:
			 - {}
			""".format(
				"\n - ".join(_state_list)
			)

			if self.flags.status_changed_to in self.flowstates.success:
				psl.handle_success(self.state.response_payload)
				processed = processed or Processed(
					gateway=self.name,
					message=_("Payment {} succeeded").format(psltype),
					action={"redirect_to": "/"},
					payload=None,
				)
			elif self.flags.status_changed_to in self.flowstates.pre_authorized:
				psl.handle_success(self.state.response_payload)
				psl.db_set("status", "Authorized", update_modified=False)
				processed = processed or Processed(
					gateway=self.name,
					message=_("Payment {} authorized").format(psltype),
					action={"redirect_to": "/"},
					payload=None,
				)
			elif self.flags.status_changed_to in self.flowstates.processing:
				psl.handle_success(self.state.response_payload)
				psl.db_set("status", "Waiting", update_modified=False)
				processed = processed or Processed(
					gateway=self.name,
					message=_("Payment {} awaiting further processing by the bank").format(psltype),
					action={"redirect_to": "/"},
					payload=None,
				)
			elif self.flags.status_changed_to in self.flowstates.failure:
				psl.handle_failure(self.state.response_payload)
				try:
					if ref_doc.hasattr("on_payment_failed"):
						msg = self._render_failure_message()
						status = self.flags.status_changed_to
						ref_doc.run_method("on_payment_failed", status, msg)
				except Exception:
					error = psl.log_error("Setting failure message on ref doc failed")
				processed = processed or Processed(
					gateway=self.name,
					message=_("Payment {} failed").format(psltype),
					action={"redirect_to": "/"},
					payload=None,
				)

			return processed

		match psl.flow_type:
			case PSLType.mandate_acquisition:
				self.state.mandate: PaymentMandate = self._get_mandate()
				processed: Processed = _process_response(
					callable=self._process_response_for_mandate_acquisition,
					hookmethod="on_payment_mandate_acquisition_processed",
					psltype="mandate adquisition",
				)
			case PSLType.mandated_charge:
				self.state.mandate: PaymentMandate = self._get_mandate()
				processed: Processed = _process_response(
					callable=self._process_response_for_mandated_charge,
					hookmethod="on_payment_mandated_charge_processed",
					psltype="mandated charge",
				)
			case PSLType.charge:
				processed: Processed = _process_response(
					callable=self._process_response_for_charge,
					hookmethod="on_payment_charge_processed",
					psltype="charge",
				)

		psl.update_status({"saved_return_value": processed.__dict__}, psl.status)
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

	def is_user_flow_initiation_delegated(self, psl_name: PSLName) -> bool:
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
		- self.state.psl
		- self.state.tx_data
		"""
		assert self.state.psl
		assert self.state.tx_data
		_help_me_develop(self.state)
		return False

	def _get_mandate(self) -> PaymentMandate:
		"""Optional: Define here, how to fetch this controller's mandate doctype instance.

		Since a mandate might be highly controller specific, this is its accessor.

		You have read (!) access to:
		- self.state.psl
		- self.state.tx_data
		"""
		assert self.state.psl
		assert self.state.tx_data
		_help_me_develop(self.state)
		return None

	def _create_mandate(self) -> PaymentMandate:
		"""Optional: Define here, how to create controller's mandate doctype instance.

		Since a mandate might be highly controller specific, this is its constructor.

		You have read (!) access to:
		- self.state.psl
		- self.state.tx_data
		"""
		assert self.state.psl
		assert self.state.tx_data
		_help_me_develop(self.state)
		return None

	def _initiate_mandate_acquisition(self) -> Initiated:
		"""Invoked by proceed to initiate a mandate acquisiton flow.

		Implementations can read:
		- self.state.psl
		- self.state.tx_data

		Implementations can read/write:
		- self.state.mandate
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _initiate_mandated_charge(self) -> Initiated:
		"""Invoked by proceed or after having aquired a mandate in order to initiate a mandated charge flow.

		Implementations can read:
		- self.state.psl
		- self.state.tx_data

		Implementations can read/write:
		- self.state.mandate
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _initiate_charge(self) -> Initiated:
		"""Invoked by proceed in order to initiate a charge flow.

		Implementations can read:
		- self.state.psl
		- self.state.tx_data
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _validate_response_payload(self) -> None:
		"""Implement how the validation of the response signature

		Implementations can read:
		- self.state.psl
		- self.state.tx_data
		- self.state.response_payload
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _process_response_for_mandate_acquisition(self) -> Processed | None:
		"""Implement how the controller should process mandate acquisition responses

		Implementations can read:
		- self.state.psl
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
		- self.state.psl
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
		- self.state.psl
		- self.state.tx_data
		- self.state.response_payload
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _render_failure_message(self) -> str:
		"""Extract a readable failure message out of the server response

		Implementations can read:
		- self.state.psl
		- self.state.tx_data
		- self.state.response_payload
		- self.state.mandate; if mandate is involved
		"""
		_help_me_develop(self.state)
		raise NotImplementedError
