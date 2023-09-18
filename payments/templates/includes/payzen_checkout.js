$(document).ready(function() {

	var doctype = "{{ reference_doctype }}"
	var docname = "{{ reference_docname }}"

	KR.onSubmit(paymentData => {
		/* return values:
		 * true: kr-post-success-url is called using POST
		 * false: kr-post-success-url is not called, execution stops.
		 */
		// return false;
		frappe.call({
			method:"payments.templates.pages.payzen_checkout.make_payment",
			freeze:true,
			headers: {"X-Requested-With": "XMLHttpRequest"},
			args: {
				"data": JSON.stringify(paymentData.clientAnswer),
				"hash": paymentData.hash,
				"reference_doctype": doctype,
				"reference_docname": docname,
			},
			callback: function(r){
				if (r.message && r.message.status == "Completed") {
					window.location.href = r.message.redirect_to
				}
				else if (r.message && r.message.status == "Error") {
					window.location.href = r.message.redirect_to
				}
				else if (r.message && r.message.status == "Running") {
					window.location.href = r.message.redirect_to
				}
			}
		})
	});
})
