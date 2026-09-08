"""Codigos HTTP que la app usa con nombre propio.

`status.HTTP_422_UNPROCESSABLE_ENTITY` quedo deprecado en Starlette y renombrado
a `..._CONTENT`. Se define aqui el numero para no atarse a ninguno de los dos
nombres ni ensuciar el codigo con avisos de deprecacion.
"""

HTTP_422 = 422
