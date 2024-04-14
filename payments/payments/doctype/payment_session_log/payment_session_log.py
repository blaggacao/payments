# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import Interval
from frappe.query_builder.functions import Now
from frappe.utils import strip_html
from frappe.utils.data import cstr

from payments.types import TxData, RemoteServerInitiationPayload, GatewayProcessingResponse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from payments.controllers import PaymentController
	from payments.payments.doctype.payment_button.payment_button import PaymentButton


class PaymentSessionLog(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		button: DF.Data | None
		correlation_id: DF.Data | None
		failure_reason: DF.Data | None
		flow_type: DF.Data | None
		gateway: DF.Data | None
		initiation_response_payload: DF.Code | None
		mandate: DF.Data | None
		processing_response_payload: DF.Code | None
		status: DF.Data | None
		title: DF.Data | None
		tx_data: DF.Code | None
	# end: auto-generated types
	def update_tx_data(self, tx_data: TxData, status: str) -> None:
		data = json.loads(self.tx_data)
		data.update(tx_data)
		self.db_set(
			{
				"tx_data": frappe.as_json(data),
				"status": status,
			},
			commit=True,
		)

	def set_initiation_payload(
		self, initiation_payload: RemoteServerInitiationPayload, status: str
	) -> None:
		self.db_set(
			{
				"initiation_response_payload": frappe.as_json(initiation_payload),
				"status": status,
			},
			commit=True,
		)

	def set_processing_payload(
		self, processing_response: GatewayProcessingResponse, status: str
	) -> None:
		self.db_set(
			{
				"processing_response_payload": frappe.as_json(processing_response.payload),
				"status": status,
			},
			commit=True,
		)

	def load_state(self):
		return frappe._dict(
			psl=frappe._dict(self.as_dict()),
			tx_data=TxData(**json.loads(self.tx_data)),
		)

	def get_controller(self) -> "PaymentController":
		"""For perfomance reasons, this is not implemented as a dynamic link but a json value
		so that it is only fetched when absolutely necessary.
		"""
		if not self.gateway:
			self.log_error("No gateway selected yet")
			frappe.throw(_("No gateway selected for this payment session"))
		d = json.loads(self.gateway)
		doctype, docname = d["gateway_settings"], d["gateway_controller"]
		return frappe.get_cached_doc(doctype, docname)

	def get_button(self) -> "PaymentButton":
		if not self.button:
			self.log_error("No button selected yet")
			frappe.throw(_("No button selected for this payment session"))
		return frappe.get_cached_doc("Payment Button", self.button)

	@staticmethod
	def clear_old_logs(days=90):
		table = frappe.qb.DocType("Payment Session Log")
		frappe.db.delete(
			table, filters=(table.modified < (Now() - Interval(days=days))) & (table.status == "Success")
		)


@frappe.whitelist(allow_guest=True)
def select_button(pslName: str = None, buttonName: str = None) -> str:
	try:
		psl = frappe.get_cached_doc("Payment Session Log", pslName)
	except Exception:
		e = frappe.log_error("Payment Session Log not found", reference_doctype="Payment Session Log")
		# Ensure no more details are leaked than the error log reference
		frappe.local.message_log = [_("Server Failure!<br>{}").format(e)]
		return
	try:
		btn: PaymentButton = frappe.get_cached_doc("Payment Button", buttonName)
	except Exception:
		e = frappe.log_error("Payment Button not found", reference_doctype="Payment Button")
		# Ensure no more details are leaked than the error log reference
		frappe.local.message_log = [_("Server Failure!<br>{}").format(e)]
		return

	psl.db_set(
		{
			"button": buttonName,
			"gateway": json.dumps(
				{
					"gateway_settings": btn.gateway_settings,
					"gateway_controller": btn.gateway_controller,
				}
			),
		}
	)
	# once state set: reload the page to activate widget
	return {"reload": True}


def create_log(
	gateway: str,
	tx_data: TxData,
	status: str = "Initiated",
	# response_data=None,
	# request_data=None,
	# exception=None,
	# rollback=False,
	# method=None,
	# message=None,
	# make_new=False,
) -> PaymentSessionLog:
	# make_new = make_new or not bool(frappe.flags.request_id)

	# if rollback:
	# frappe.db.rollback()

	if True:  # make_new:
		log = frappe.new_doc("Payment Session Log")
		log.gateway = gateway
		log.tx_data = frappe.as_json(tx_data)
		log.status = status
		log.insert(ignore_permissions=True)
	else:
		log = frappe.get_doc("Payment Session Log", frappe.flags.request_id)

	# if response_data and not isinstance(response_data, str):
	# 	response_data = json.dumps(response_data, sort_keys=True, indent=4)

	# if request_data and not isinstance(request_data, str):
	# 	request_data = json.dumps(request_data, sort_keys=True, indent=4)

	# log.message = message or _get_message(exception)
	# log.method = log.method or method
	# log.response_data = response_data or log.response_data
	# log.request_data = request_data or log.request_data
	# log.traceback = log.traceback or frappe.get_traceback()
	# log.status = status
	# log.save(ignore_permissions=True)

	frappe.db.commit()

	return log


def _get_message(exception):
	if hasattr(exception, "message"):
		return strip_html(exception.message)
	elif hasattr(exception, "__str__"):
		return strip_html(exception.__str__())
	else:
		return _("Something went wrong while syncing")


@frappe.whitelist()
def resync(method, name, request_data):
	_retry_job(name)


def _retry_job(job: str):
	frappe.only_for("System Manager")

	doc = frappe.get_doc("Payment Session Log", job)
	if not doc.method.startswith("payments.payment_gateways.") or doc.status != "Error":
		return

	doc.db_set("status", "Queued", update_modified=False)
	doc.db_set("traceback", "", update_modified=False)

	frappe.enqueue(
		method=doc.method,
		queue="short",
		timeout=300,
		is_async=True,
		payload=json.loads(doc.request_data),
		request_id=doc.name,
		enqueue_after_commit=True,
	)


@frappe.whitelist()
def bulk_retry(names):
	if isinstance(names, str):
		names = json.loads(names)
	for name in names:
		_retry_job(name)
