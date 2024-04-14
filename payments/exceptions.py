from frappe.exceptions import ValidationError


class FailedToInitiateFlowError(Exception):
	def __init__(self, message, data):
		self.message = message
		self.data = data


class PayloadIntegrityError(ValidationError):
	def __init__(self, message, data):
		self.message = message
		self.data = data
