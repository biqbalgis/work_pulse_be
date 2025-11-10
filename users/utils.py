"""Utility functions for user management."""
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
import logging

logger = logging.getLogger(__name__)


def send_invitation_email(user, invitation_url):
    """Send invitation email to a new user."""
    try:
        subject = 'You\'ve been invited to join WorkPulse'
        
        # Create HTML email
        html_message = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <h2 style="color: #1976d2;">Welcome to WorkPulse!</h2>
                <p>Hello {user.first_name},</p>
                <p>You have been invited to join WorkPulse. To complete your registration and set up your account, please click the button below:</p>
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{invitation_url}" style="background-color: #1976d2; color: white; padding: 12px 30px; text-decoration: none; border-radius: 5px; display: inline-block;">Complete Registration</a>
                </div>
                <p>Or copy and paste this link into your browser:</p>
                <p style="word-break: break-all; color: #666;">{invitation_url}</p>
                <p>This invitation link will expire in 7 days.</p>
                <p>If you did not expect this invitation, please ignore this email.</p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
                <p style="color: #666; font-size: 12px;">This is an automated message, please do not reply.</p>
            </div>
        </body>
        </html>
        """
        
        plain_message = strip_tags(html_message)
        
        from_email = settings.DEFAULT_FROM_EMAIL if hasattr(settings, 'DEFAULT_FROM_EMAIL') else 'noreply@workpulse.com'
        
        send_mail(
            subject=subject,
            message=plain_message,
            from_email=from_email,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
        
        logger.info(f"Invitation email sent to {user.email}")
        return True
    except Exception as e:
        logger.error(f"Error sending invitation email to {user.email}: {e}")
        return False


def generate_invitation_token(user):
    """Generate an invitation token for a user."""
    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    return uid, token


def verify_invitation_token(uid, token):
    """Verify an invitation token."""
    try:
        from django.utils.http import urlsafe_base64_decode
        from django.utils.encoding import force_str
        from users.models import User
        
        user_id = force_str(urlsafe_base64_decode(uid))
        user = User.objects.get(pk=user_id)
        
        if default_token_generator.check_token(user, token):
            return user
        return None
    except Exception as e:
        logger.error(f"Error verifying invitation token: {e}")
        return None

