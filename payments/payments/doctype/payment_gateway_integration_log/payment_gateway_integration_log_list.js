frappe.listview_settings["Payment Gateway Integration Log"] = {
	hide_name_column: true,
	add_fields: ["status"],
	get_indicator: function (doc) {
		if (doc.status === "Success") {
			return [__("Success"), "green", "status,=,Success"];
		} else if (doc.status === "Error") {
			return [__("Error"), "red", "status,=,Error"];
		} else if (doc.status === "Queued") {
			return [__("Queued"), "orange", "status,=,Queued"];
		}
	},

	onload: function (listview) {
		listview.page.add_action_item(__("Retry"), () => {
			listview.call_for_selected_items(
				"payments.payments.doctype.payment_gateway_integration_log.payment_gateway_integration_log.bulk_retry"
			);
		});
	},
};
