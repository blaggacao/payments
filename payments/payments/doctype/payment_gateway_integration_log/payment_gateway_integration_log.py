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

from payments.types import TxData

from types import MappingProxyType


class PaymentGatewayIntegrationLog(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		correlation_id: DF.Data | None
		flow_type: DF.Data | None
		gateway: DF.Data | None
		initiation_return_payload: DF.SmallText | None
		mandate: DF.Data | None
		message: DF.Code | None
		response_data: DF.Code | None
		status: DF.Data | None
		title: DF.Data | None
		traceback: DF.Code | None
		tx_data: DF.Code | None
	# end: auto-generated types
	def validate(self):
		self._set_title()

	def _set_title(self):
		title = None
		if self.message != "None":
			title = self.message

		if not title and self.method:
			method = self.method.split(".")[-1]
			title = method

		if title:
			title = strip_html(title)
			self.title = title if len(title) < 100 else title[:100] + "..."

	def update_tx_data(self, tx_data: TxData, status: str) -> None:
		data = json.loads(self.tx_data)
		data.update(tx_data)
		self.tx_data = frappe.as_json(data)
		self.status = status
		self.save(ignore_permissions=True)
		frappe.db.commit()

	def load_state(self):
		return frappe._dict(
			ilog=MappingProxyType(self.as_dict()),
			tx_data=MappingProxyType(json.loads(self.tx_data)),
		)

	@staticmethod
	def clear_old_logs(days=90):
		table = frappe.qb.DocType("Payment Gateway Integration Log")
		frappe.db.delete(
			table, filters=(table.modified < (Now() - Interval(days=days))) & (table.status == "Success")
		)


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
) -> PaymentGatewayIntegrationLog:
	# make_new = make_new or not bool(frappe.flags.request_id)

	# if rollback:
	# frappe.db.rollback()

	if True:  # make_new:
		log = frappe.new_doc("Payment Gateway Integration Log")
		log.gateway = gateway
		log.tx_data = frappe.as_json(tx_data)
		log.status = status
		log.insert(ignore_permissions=True)
	else:
		log = frappe.get_doc("Payment Gateway Integration Log", frappe.flags.request_id)

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

	doc = frappe.get_doc("Payment Gateway Integration Log", job)
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
