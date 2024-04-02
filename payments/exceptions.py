from frappe.exceptions import ValidationError


class InitiationError(Exception):
	pass


class PayloadIntegrityError(ValidationError):
	pass
