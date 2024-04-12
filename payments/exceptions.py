from frappe.exceptions import ValidationError


class FailedToInitiateFlowError(Exception):
	pass


class PayloadIntegrityError(ValidationError):
	pass
