import json

import frappe
from frappe import _
from payments.utils import PAYMENT_SESSION_REF_KEY
from payments.controllers import PaymentController
from payments.types import Proceeded, TxData

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from payments.payments.doctype.payment_session_log.payment_session_log import PaymentSessionLog

no_cache = 1


def get_psl():
	try:
		return frappe.form_dict[PAYMENT_SESSION_REF_KEY]
	except KeyError:
		frappe.redirect_to_message(
			_("Invalid Payment Link"),
			_("This payment link is invalid!"),
			http_status_code=400,
			indicator_color="red",
		)
		raise frappe.Redirect


default_icon = """
<svg style="shape-rendering:geometricPrecision; text-rendering:geometricPrecision; image-rendering:optimizeQuality; fill-rule:evenodd; clip-rule:evenodd" version="1.1" viewBox="0 0 270.92 270.92">
<g id="Layer_x0020_1"><path class="fil0" d="M135.48 160.83c-4.8,0 -8.73,-3.91 -8.73,-8.7 0,-4.4 -3.53,-7.95 -7.93,-7.95 -4.39,0 -7.93,3.55 -7.93,7.95 0,10.75 6.99,19.83 16.65,23.15l0 4.49c0,4.38 3.55,7.95 7.94,7.95 4.38,0 7.93,-3.57 7.93,-7.95l0 -4.49c9.66,-3.32 16.65,-12.4 16.65,-23.15 0,-13.58 -11.03,-24.61 -24.58,-24.61 -4.8,0 -8.73,-3.91 -8.73,-8.71 0,-4.81 3.93,-8.72 8.73,-8.72 4.79,0 8.72,3.91 8.72,8.72 0,4.38 3.55,7.94 7.94,7.94 4.38,0 7.92,-3.56 7.92,-7.94 0,-10.77 -6.99,-19.83 -16.65,-23.16l0 -4.51c0,-4.38 -3.55,-7.94 -7.93,-7.94 -4.39,0 -7.94,3.56 -7.94,7.94l0 4.51c-9.66,3.33 -16.65,12.39 -16.65,23.16 0,13.56 11.02,24.58 24.59,24.58 4.79,0 8.72,3.91 8.72,8.74 0,4.79 -3.93,8.7 -8.72,8.7zm-69.24 46l-14.21 0c-10.9,-0.24 -19.72,-9.16 -19.72,-20.13l0 -13.76c17.12,3.25 30.66,16.79 33.93,33.89zm172.4 -33.89l0 13.76c0,10.97 -8.81,19.89 -19.7,20.13l-14.26 -0.01c3.27,-17.1 16.84,-30.65 33.96,-33.88zm-33.96 -108.91l13.79 0c11.1,0 20.14,9.04 20.16,20.14l-0.01 13.75c-17.11,-3.26 -30.67,-16.79 -33.94,-33.89zm17.56 -15.86l-170.55 0c-4.38,0 -7.94,3.56 -7.94,7.93 0,4.37 3.56,7.93 7.94,7.93l136.97 0c3.57,25.85 24.1,46.38 49.98,49.91l0 42.99c-25.9,3.54 -46.44,24.08 -49.98,49.95l-106.4 0c-3.54,-25.87 -24.06,-46.39 -49.95,-49.95l0 -73.49c0,-4.39 -3.56,-7.94 -7.94,-7.94 -4.39,0 -7.94,3.55 -7.94,7.94l0 82.36c0,0.13 -0.02,0.25 -0.02,0.4l0 24.24c0,17.79 14.47,32.27 32.28,32.27l3.34 0c0.15,0 0.3,0.04 0.45,0.04l165.99 0c0.15,0 0.31,-0.04 0.47,-0.04l3.3 0c17.81,0 32.27,-14.48 32.27,-32.27l0 -110.02c0,-17.77 -14.46,-32.25 -32.27,-32.25z"/></g>
</svg>
"""


def get_context(context):

	# always

	# psl: PaymentSessionLog = frappe.get_doc("Payment Session Log", get_psl())

	psl = None

	# state = psl.load_state()

	# context.tx_data: TxData = state.tx_data
	context.tx_data = TxData(
		amount=12348.00,
		currency="COP",
		reference_doctype="Sales Order",
		reference_docname="SAL-ORD-2024-0001",
		payer_contact={"full_name": "Lina Avendano"},
		payer_address={},
	).__dict__

	context.payment_buttons = [
		(default_icon, "Payzen Settings[Bancolombia]", "Bancolombia"),
		(default_icon, "Payzen Settings[Colpatria]", "Colpatria"),
		(default_icon, "Payzen Settings[CARDS]", "Tarjeta Credito"),
		(default_icon, "Payzen Settings[PSE]", "PSE"),
	]

	# only when gateway has already been selected

	if False and psl.gateway:
		context.is_gateway_selected = True
		tx_update = {}  # TODO: implement that the user may change some values
		proceede: Proceeded = PaymentController.proceed(psl.name, tx_update)

		context.gateway_css = """
            <link rel="stylesheet" href="{{ static_assets_url }}/js/krypton-client/V4.0/ext/neon-reset.min.css">
        """
		context.gateway_js = """
            <script src="{{ static_assets_url }}/js/krypton-client/V4.0/stable/kr-payment-form.min.js"
              kr-public-key="{{ kr_public_key }}"></script>
            <script src="{{ static_assets_url }}/js/krypton-client/V4.0/ext/neon.js"></script>
        """

		context.gateway_widget = """
            <div class="wrapper">
              <div class="checkout container">
                <div id="payment-form" class="container">
                  <img id="header-img" class="center" src="{{ header_img }}"></img>

                  <!-- payment form -->
                  <div class="kr-smart-button" kr-payment-method="PSE"></div>
                  <div class="kr-smart-button" kr-payment-method="CARDS"></div>
                  <div class="kr-smart-form" kr-form-token="{{ client_token }}"></div>
                  <!-- error zone -->
                  <div class="kr-form-error"></div>
                </div>
              </div>
            </div>

            <style>
              .kr-smart-button {
                margin-left: auto;
                margin-right: auto;
              }

              .kr-form-error {
                margin-left: auto;
                margin-right: auto;
              }

              .kr-form-error>span {
                margin-left: auto;
                margin-right: auto;
              }

              #payment-form {
                margin-top: 40px;
              }

              #header-img {
                margin-bottom: 40px;
                margin: auto;
                display: block;
              }
            </style>
        """
