$(document).ready(function() {
	KR.onSubmit(paymentData => {
		/* return values:
		 * true: kr-post-success-url is called using POST
		 * false: kr-post-success-url is not called, execution stops.
		 */
		// return false;
		frappe.call({
			method:"payments.controllers.PaymentController.process_response",
			freeze:true,
			headers: {"X-Requested-With": "XMLHttpRequest"},
			args: {
				"psl_name": paymentData.metadata.psl,
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
