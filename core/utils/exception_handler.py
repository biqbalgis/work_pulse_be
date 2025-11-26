from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.http import Http404

def custom_exception_handler(exc, context):
    """
    Custom exception handler that handles Django ValidationErrors and other exceptions
    that DRF's default handler misses.
    """
    # Call REST framework's default exception handler first
    response = exception_handler(exc, context)

    # If response is None, there may be a generic Django exception
    if response is None:
        if isinstance(exc, ValidationError):
            # Handle Django ValidationError
            # It can be a list, a dict, or a string
            if hasattr(exc, 'message_dict'):
                detail = exc.message_dict
            elif hasattr(exc, 'messages'):
                detail = exc.messages
            else:
                detail = str(exc)
                
            return Response(
                {"error": "Validation Error", "detail": detail}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if isinstance(exc, ObjectDoesNotExist):
            return Response(
                {"error": "Not Found", "detail": "The requested resource was not found."}, 
                status=status.HTTP_404_NOT_FOUND
            )
            
        if isinstance(exc, ValueError):
            # Often happens with UUID conversion or date parsing
            return Response(
                {"error": "Invalid Value", "detail": str(exc)}, 
                status=status.HTTP_400_BAD_REQUEST
            )

    return response
