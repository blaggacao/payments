$(document).ready(function() {
	KR.onSubmit(paymentData => {
		/* return values:
		 * true: kr-post-success-url is called using POST
		 * false: kr-post-success-url is not called, execution stops.
		 */
		// return false;
		frappe.call({
			method:"payments.controllers.payment_gateway_controller.PaymentGatewayController.process_response",
			freeze:true,
			headers: {"X-Requested-With": "XMLHttpRequest"},
			args: {
				"ilog_name": paymentData.metadata.ilog,
				"payload": {
					"data":	JSON.stringify(paymentData),
					"hash": paymentData.hash,
				},
			},
			callback: function(r){
				if (r.message && r.message.redirect_to) {
					window.location.href = r.message.redirect_to
				}
			}
		})
	});
})
