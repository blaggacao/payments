// Copyright (c) 2021, Frappe and contributors
// For license information, please see LICENSE

frappe.ui.form.on('Payment Gateway Integration Log', {
	refresh: function(frm) {
		if (frm.doc.request_data && frm.doc.status=='Error'){
			frm.add_custom_button('Retry', function() {
				frappe.call({
					method:"payments.payments.doctype.payment_gateway_integration_log.payment_gateway_integration_log.resync",
					args:{
						method:frm.doc.method,
						name: frm.doc.name,
						request_data: frm.doc.request_data
					},
					callback: function(r){
						frappe.msgprint(__("Reattempting to sync"))
					}
				})
			}).addClass('btn-primary');
		}
	}
});
