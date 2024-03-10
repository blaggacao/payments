# Copyright (c) 2018, Frappe Technologies and contributors
# License: MIT. See LICENSE

from urllib.parse import urlencode

import hashlib
import hmac
import json

import frappe
from frappe import _
from frappe.integrations.utils import create_request_log, make_post_request
from frappe.model.document import Document
from frappe.utils import call_hook_method, get_url

from requests.auth import HTTPBasicAuth

from payments.utils import create_payment_gateway


class PayzenSettings(Document):
	supported_currencies = [
		"COP",
	]

	# source: https://github.com/lyra/flask-embedded-form-examples/blob/master/.env.example
	static_urls = {
		"Clic&Pay By groupe Crédit du Nord": "https://api-clicandpay.groupecdn.fr/static/",
		"Cobro Inmediato": "https://static.cobroinmediato.tech/static/",
		"EpayNC": "https://epaync.nc/static/",
		"Lyra Collect": "https://api.lyra.com/static/",
		"Mi Cuenta Web": "https://static.micuentaweb.pe/static/",
		"Payty": "https://static.payty.com/static/",
		"PayZen India": "https://secure.payzen.co.in/static/",
		"PayZen LATAM": "https://static.payzen.lat/static/",
		"PayZen Brazil": "https://api.payzen.com.br/api-payment/",
		"PayZen Europe": "https://static.payzen.eu/static/",
		"Scellius": "https://api.scelliuspaiement.labanquepostale.fr/static/",
		"Sogecommerce": "https://api-sogecommerce.societegenerale.eu/static/",
		"Systempay": "https://api.systempay.fr/static/",
	}

	# source: https://github.com/lyra/flask-embedded-form-examples/blob/master/.env.example
	api_urls = {
		"Clic&Pay By groupe Crédit du Nord": "https://api-clicandpay.groupecdn.fr/api-payment/",
		"Cobro Inmediato": "https://api.cobroinmediato.tech/api-payment/",
		"EpayNC": "https://epaync.nc/api-payment/",
		"Lyra Collect": "https://api.lyra.com/api-payment/",
		"Mi Cuenta Web": "https://api.micuentaweb.pe/api-payment/",
		"Payty": "https://api.payty.com/api-payment/",
		"PayZen India": "https://secure.payzen.co.in/api-payment/",
		"PayZen LATAM": "https://api.payzen.lat/api-payment/",
		"PayZen Brazil": "https://static.payzen.lat/static/",
		"PayZen Europe": "https://api.payzen.eu/api-payment/",
		"Scellius": "https://api.scelliuspaiement.labanquepostale.fr/api-payment/",
		"Sogecommerce": "https://api-sogecommerce.societegenerale.eu/api-payment/",
		"Systempay": "https://api.systempay.fr/api-payment/",
	}

	def validate(self):
		self.set_read_only_fields()
		if not self.flags.ignore_mandatory:
			self.validate_payzen_credentials()

	def on_update(self):
		create_payment_gateway(
			"Payzen-" + self.gateway_name,
			settings="Payzen Settings",
			controller=self.gateway_name,
		)
		call_hook_method("payment_gateway_enabled", gateway="Payzen-" + self.gateway_name)

	def on_payment_request_submission(self, pr):
		if not pr.grand_total:
			frappe.throw(_("Payment amount cannot be 0"))
		self.validate_transaction_currency(pr.currency)
		return True

	def set_read_only_fields(self):
		self.api_url = self.api_urls.get(self.brand)
		self.static_assets_url = self.static_urls.get(self.brand)

	def validate_payzen_credentials(self):
		def make_test_request(auth):
			return frappe._dict(
				make_post_request(url=f"{self.api_url}/V4/Charge/SDKTest", auth=auth, data={"value": "test"})
			)

		if self.test_password:
			try:
				password = self.get_password(fieldname="test_password")
				result = make_test_request(HTTPBasicAuth(self.shop_id, password))
				if result.status != "SUCCESS" or result.answer.get("value") != "test":
					frappe.throw(_("Test credentials seem not valid."))
			except Exception:
				frappe.throw(_("Could not validate test credentials."))

		if self.production_password:
			try:
				password = self.get_password(fieldname="production_password")
				result = make_test_request(HTTPBasicAuth(self.shop_id, password))
				if result.status != "SUCCESS" or result.answer.get("value") != "test":
					frappe.throw(_("Production credentials seem not valid."))
			except Exception:
				frappe.throw(_("Could not validate production credentials."))

	def validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(
				_(
					"Please select another payment method. Payzen does not support transactions in currency '{0}'"
				).format(currency)
			)

	def get_payment_url(self, **kwargs):
		url = frappe.get_doc(
			{
				"doctype": "Shortener",
				"long_url": get_url(f"./payzen_checkout?{urlencode(kwargs)}"),
			}
		)
		url.insert(ignore_permissions=True)
		return url.short_url

	def get_fields_for_rendering_context(self):
		pubkey = self.test_public_key if self.use_sandbox else self.production_public_key
		return {
			"static_assets_url": self.static_assets_url,
			"header_img": self.header_img,
			"kr_public_key": f"{self.shop_id}:{pubkey}",
		}

	def finalize_payment_request(self, data, hash, reference_doctype, reference_docname):
		key = self.get_password(
			fieldname="test_hmac_key" if self.use_sandbox else "production_hmac_key",
			raise_exception=False,
		)
		signature = compute_hmac_sha256_signature(key, data)

		data = frappe._dict(json.loads(data))
		reportedOrderStatus = data.orderStatus
		validatedOrderStatus = reportedOrderStatus if hash == signature else False

		# If answer is forged, return early
		if hash != signature:
			return {"redirect_to": "payment-failed", "status": "Error"}

		integration_request = frappe.get_last_doc(
			"Integration Request",
			filters={"reference_doctype": reference_doctype, "reference_docname": reference_docname},
		)
		metadata = frappe._dict(json.loads(integration_request.data).get("metadata"))

		redirect_to = metadata.get("redirect_to") or None
		redirect_message = metadata.get("redirect_message") or None

		if not redirect_to:
			try:
				redirect_to = frappe.get_doc(
					reference_doctype,
					reference_docname,
				).run_method("on_payment_authorized_redirect")
			except Exception:
				pass

		if integration_request.status == "Completed" or validatedOrderStatus == "PAID":
			status = "Completed"
			redirect_url = f"payment-success?doctype={reference_doctype}&docname={reference_docname}"
			redirect_message = redirect_message or _(
				f"Your {reference_doctype} ({reference_docname}) was successfully paid."
			)
		elif validatedOrderStatus == "RUNNING":
			status = "Running"
			redirect_url = "payment-running"
		else:  # UNPAID / ABANDONED
			status = "Error"
			redirect_url = "payment-failed"

		if redirect_to:
			param = urlencode({"redirect_to": redirect_to})
			redirect_url += ("?" if "?" not in redirect_url else "&") + param

		if redirect_message:
			param = urlencode({"redirect_message": redirect_message})
			redirect_url += ("?" if "?" not in redirect_url else "&") + param

		return {"redirect_to": redirect_url, "status": status}


def get_gateway_controller(reference_doctype, reference_docname):
	doc = frappe.get_doc(reference_doctype, reference_docname)
	gateway_controller = frappe.db.get_value(
		"Payment Gateway", doc.payment_gateway, "gateway_controller"
	)
	return gateway_controller


def get_form_token(reference_doctype, reference_docname, form):
	gateway_controller = get_gateway_controller(reference_doctype, reference_docname)
	settings = frappe.get_doc("Payzen Settings", gateway_controller)

	data = {
		"amount": form["amount"],
		"currency": form["currency"],
		"orderId": form["order_id"],
		"customer": {
			"reference": form["payer_name"],
		},
		"strongAuthentication": settings.challenge_3ds,
		"contrib": f"ERPNext/{gateway_controller}",
		"ipnTargetUrl": get_url(
			"./api/method/payments.payment_gateways.doctype.payzen_settings.payzen_settings.notification"
		),
		"metadata": {
			"reference_doctype": reference_doctype,
			"reference_docname": reference_docname,
		},
	}

	if form.get("payer_email"):
		data["customer"]["email"] = form["payer_email"]

	if not frappe.db.exists("Integration Request", reference_docname, cache=True):
		integration_request = create_request_log(
			data,
			url=f"{settings.api_url}/V4/Charge/CreatePayment",
			name=reference_docname,
			service_name="Payzen",
			reference_doctype=reference_doctype,
			reference_docname=reference_docname,
		)
	else:
		integration_request = frappe.get_last_doc(
			"Integration Request",
			filters={"reference_doctype": reference_doctype, "reference_docname": reference_docname},
		)

	res = make_post_request(
		url=integration_request.url,
		auth=HTTPBasicAuth(
			settings.shop_id,
			settings.get_password(
				fieldname="test_password" if settings.use_sandbox else "production_password",
				raise_exception=False,
			),
		),
		json=data,
	)

	return res


def compute_hmac_sha256_signature(key, message):
	"""
	`key` argument is the password of the store
	`message` argument is all the arguments concatenated, plus the password store
	"""
	byte_key = str.encode(key)
	message = str.encode(message)
	signature = hmac.new(byte_key, message, hashlib.sha256).hexdigest()
	return signature


@frappe.whitelist(allow_guest=True, methods=["POST"])
def notification(**kwargs):
	kr_hash = kwargs["kr-hash"]
	kr_hash_key = kwargs["kr-hash-key"]
	kr_hash_algorithm = kwargs["kr-hash-algorithm"]
	kr_answer = kwargs["kr-answer"]
	kr_answer_type = kwargs["kr-answer-type"]

	if not kr_answer:
		return

	# Validate signature for given payment order id (= Payment Request)
	data = frappe._dict(json.loads(kr_answer))
	tx = data.transactions[0]
	metadata = frappe._dict(tx.get("metadata"))
	reference_doctype, reference_docname = metadata.reference_doctype, metadata.reference_docname
	gateway_controller = get_gateway_controller(reference_doctype, reference_docname)
	settings = frappe.get_doc("Payzen Settings", gateway_controller)
	key = settings.get_password(
		fieldname="test_password" if settings.use_sandbox else "production_password",
		raise_exception=False,
	)

	signature = compute_hmac_sha256_signature(key, kr_answer)
	if not kr_hash == signature:
		return

	# Answer is valid

	integration_request = frappe.get_last_doc(
		"Integration Request",
		filters={"reference_doctype": reference_doctype, "reference_docname": reference_docname},
	)
	if integration_request.status == "Completed":
		return

	if data.orderStatus == "PAID":
		integration_request.handle_success(data)
		try:
			frappe.get_doc(
				reference_doctype,
				reference_docname,
			).run_method("on_payment_authorized", integration_request.status)
		except Exception as e:
			frappe.log_error(
				"on_payment_authorized() failed",
				frappe.get_traceback(),
				reference_doctype,
				reference_docname,
			)
	elif data.orderStatus == "RUNNING":
		integration_request.db_set("output", json.dumps(data))
	else:
		integration_request.handle_failure(data)
		try:
			txDetails = data["transactions"][0]
			frappe.get_doc(reference_doctype, reference_docname,).run_method(
				"on_payment_failed",
				integration_request.status,
				f"{txDetails.get('detailedErrorCode', 'NO ERROR CODE')}: {txDetails.get('detailedErrorMessage', 'no detail')}",
			)
		except Exception as e:
			frappe.log_error(
				"on_payment_failed() failed",
				frappe.get_traceback(),
				reference_doctype,
				reference_docname,
			)
