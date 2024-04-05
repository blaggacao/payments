import json

import frappe
from frappe import _
from payments.utils import TX_REFERENCE_KEY, recover_references
from payments.controllers import PaymentController
from payments.types import Proceeded

no_cache = 1


def get_psl():
	try:
		psl_name = frappe.form_dict[TX_REFERENCE_KEY]
	except KeyError:
		frappe.redirect_to_message(
			_("Invalid Payment Link"),
			_("This payment link is invalid!"),
			http_status_code=400,
			indicator_color="red",
		)
		# raise frappe.Redirect
	else:
		return psl_name


def get_context(context):

	psl_name = get_psl()

	# proceeded: Proceeded = PaymentController.proceed(psl_name, tx_update)

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
