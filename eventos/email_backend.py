import ssl

from django.core.mail.backends.smtp import EmailBackend as SMTPEmailBackend


class EmailBackend(SMTPEmailBackend):

    def open(self):
        if self.connection:
            return False

        try:
            context = ssl.create_default_context()

            context.verify_flags &= ~ssl.VERIFY_X509_STRICT

            self.ssl_context = context

            return super().open()

        except Exception:
            if not self.fail_silently:
                raise

            return False