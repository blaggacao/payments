frappe.ready(function() {

	// Focus the first button
	// document.getElementById("primary-button").focus();

	// Get all button elements
	const buttons = Array.from(document.getElementsByClassName('btn-pay'));

	// Get the error section
	// const errors = document.getElementById("errors");

	// Get the payment session log name
	const urlParams = new URLSearchParams(window.location.search);
	const pslName = urlParams.get('s');

	// Loop through each button and add the onclick event listener
	buttons.forEach((button) => {
    // Get the data-button attribute value
    const buttonData = button.getAttribute('data-button');

	  button.addEventListener('click', () => {

	    // Make the Frappe call
	    frappe.call({
	      method: "payments.payments.doctype.payment_session_log.payment_session_log.select_button",
	      args: {
					pslName: pslName,
					buttonName: buttonData,
				},
				error_msg: "#errors",
	      callback: (r) => {
					if (r.message.reload) {
						window.location.reload();
					}
	      }
	    });
	  });
	});
});
