# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.utils import cint, flt

from payments.payment_gateways.doctype.payzen_settings.payzen_settings import (
	get_form_token,
	get_gateway_controller,
)

no_cache = 1

expected_keys = (
	"amount",
	"title",
	"description",
	"reference_doctype",
	"reference_docname",
	"payer_name",
	"payer_email",
	"order_id",
	"currency",
)


def get_context(context):
	context.no_cache = 1

	# all these keys exist in form_dict
	if not (set(expected_keys) - set(list(frappe.form_dict))):
		for key in expected_keys:
			context[key] = frappe.form_dict[key]

		data = frappe.form_dict.copy()

		# payzen receives values in the currency's smallest denomination
		data["amount"] = cint(flt(context["amount"]) * 100)
		res = get_form_token(context.reference_docname, data)

		if not res.get("status") == "SUCCESS":
			error_log = frappe.log_error("Payzen form token request error", res)
			frappe.redirect_to_message(
				_("Some information is missing"),
				_(
					"Looks like someone sent you to an incomplete URL. "
					"Please ask them to look into it and mention the following error log: "
					f"<b>{error_log}</b>"
				),
			)
			frappe.local.flags.redirect_location = frappe.local.response.location
			raise frappe.Redirect

		context.client_token = res["answer"]["formToken"]
		context.amount = data.amount

		gateway_controller = get_gateway_controller(context.reference_docname)
		settings = frappe.get_doc("Payzen Settings", gateway_controller)
		context.update(settings.get_fields_for_rendering_context())

	else:
		frappe.redirect_to_message(
			_("Some information is missing"),
			_(
				"Looks like someone sent you to an incomplete URL. Please ask them to look into it."
			),
		)
		frappe.local.flags.redirect_location = frappe.local.response.location
		raise frappe.Redirect

@frappe.whitelist(allow_guest=True)
def make_payment(data, hash, reference_doctype, reference_docname):
	gateway_controller = get_gateway_controller(reference_docname)
	data = frappe.get_doc("Payzen Settings", gateway_controller).finalize_payment_request(
		data,
		hash,
		reference_doctype,
		reference_docname,
	)
	frappe.db.commit()
	return data
