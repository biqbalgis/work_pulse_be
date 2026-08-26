from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone


def _send_templated_email(subject, to_email, template_base, context):
    text_body = render_to_string(f"emails/{template_base}.txt", context)
    html_body = render_to_string(f"emails/{template_base}.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
    )
    message.attach_alternative(html_body, "text/html")
    message.send()


def send_password_reset_email(user, reset_url, expiry_minutes):
    context = {
        "first_name": user.first_name or user.email,
        "email": user.email,
        "reset_url": reset_url,
        "expiry_minutes": expiry_minutes,
    }
    _send_templated_email("Reset your WorkPulse password", user.email, "password_reset", context)


def send_password_changed_email(user):
    context = {
        "first_name": user.first_name or user.email,
        "email": user.email,
        "changed_at": timezone.localtime(timezone.now()).strftime("%B %d, %Y at %H:%M %Z"),
    }
    _send_templated_email("Your WorkPulse password was changed", user.email, "password_changed", context)
