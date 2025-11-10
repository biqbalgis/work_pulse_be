from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum, Count, Avg, Q
from django.utils import timezone
from django.http import HttpResponse, JsonResponse
from datetime import datetime, timedelta, date
from decimal import Decimal
import csv
import io
import logging

from core.models import TimeEntry, Project, LeaveRequest, Expense
from users.models import UserProfile
from core.permissions import CanViewReports, IsSameOrganization

logger = logging.getLogger(__name__)


class ReportsViewSet(viewsets.ViewSet):
    """ViewSet for generating various reports."""
    permission_classes = [permissions.IsAuthenticated, CanViewReports]

    def get_queryset(self):
        """Get base queryset filtered by user's organization."""
        user_profile = self.request.user.profile
        return TimeEntry.objects.filter(project__organization=user_profile.organization)

    @action(detail=False, methods=['get'])
    def time_summary(self, request):
        """Get time summary report."""
        user_profile = request.user.profile
        
        # Get query parameters
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        user_ids = request.query_params.getlist('user_ids')
        project_ids = request.query_params.getlist('project_ids')
        client_ids = request.query_params.getlist('client_ids')
        
        # Set default date range if not provided
        if not start_date:
            start_date = (timezone.now() - timedelta(days=30)).date()
        else:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        
        if not end_date:
            end_date = timezone.now().date()
        else:
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        
        # Build queryset
        queryset = TimeEntry.objects.filter(
            project__organization=user_profile.organization,
            start_time__date__gte=start_date,
            start_time__date__lte=end_date,
            end_time__isnull=False
        )
        
        # Apply filters
        if user_ids:
            queryset = queryset.filter(user_id__in=user_ids)
        if project_ids:
            queryset = queryset.filter(project_id__in=project_ids)
        if client_ids:
            queryset = queryset.filter(project__client_id__in=client_ids)
        
        # Calculate totals
        total_hours = queryset.aggregate(total=Sum('duration'))['total'] or Decimal('0')
        billable_hours = queryset.filter(is_billable=True).aggregate(total=Sum('duration'))['total'] or Decimal('0')
        non_billable_hours = total_hours - billable_hours
        
        # Get data by user
        user_data = []
        users = UserProfile.objects.filter(organization=user_profile.organization)
        if user_ids:
            users = users.filter(id__in=user_ids)
        
        for user in users:
            user_entries = queryset.filter(user=user)
            user_total = user_entries.aggregate(total=Sum('duration'))['total'] or Decimal('0')
            user_billable = user_entries.filter(is_billable=True).aggregate(total=Sum('duration'))['total'] or Decimal('0')
            user_non_billable = user_total - user_billable
            
            user_data.append({
                'user_id': str(user.id),
                'user_name': user.user.get_full_name(),
                'total_hours': float(user_total),
                'billable_hours': float(user_billable),
                'non_billable_hours': float(user_non_billable),
                'utilization_percentage': user.get_utilization_percentage('week')
            })
        
        # Get data by project
        project_data = []
        projects = Project.objects.filter(organization=user_profile.organization)
        if project_ids:
            projects = projects.filter(id__in=project_ids)
        
        for project in projects:
            project_entries = queryset.filter(project=project)
            project_total = project_entries.aggregate(total=Sum('duration'))['total'] or Decimal('0')
            project_billable = project_entries.filter(is_billable=True).aggregate(total=Sum('duration'))['total'] or Decimal('0')
            project_non_billable = project_total - project_billable
            
            project_data.append({
                'project_id': str(project.id),
                'project_name': project.name,
                'client_name': project.client.name,
                'total_hours': float(project_total),
                'billable_hours': float(project_billable),
                'non_billable_hours': float(project_non_billable),
                'hourly_rate': float(project.hourly_rate or 0),
                'total_revenue': float(project_billable * (project.hourly_rate or 0))
            })
        
        return Response({
            'summary': {
                'total_hours': float(total_hours),
                'billable_hours': float(billable_hours),
                'non_billable_hours': float(non_billable_hours),
                'period': {
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat()
                }
            },
            'by_user': user_data,
            'by_project': project_data
        })

    @action(detail=False, methods=['get'])
    def utilization(self, request):
        """Get utilization report."""
        user_profile = request.user.profile
        
        # Get query parameters
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        user_ids = request.query_params.getlist('user_ids')
        
        # Set default date range if not provided
        if not start_date:
            start_date = (timezone.now() - timedelta(days=30)).date()
        else:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        
        if not end_date:
            end_date = timezone.now().date()
        else:
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        
        # Get users
        users = UserProfile.objects.filter(organization=user_profile.organization)
        if user_ids:
            users = users.filter(id__in=user_ids)
        
        utilization_data = []
        for user in users:
            # Calculate working days in the period
            working_days = 0
            current_date = start_date
            while current_date <= end_date:
                if current_date.weekday() in user.working_days:
                    working_days += 1
                current_date += timedelta(days=1)
            
            # Calculate capacity hours
            capacity_hours = user.daily_capacity * working_days
            
            # Calculate actual hours
            actual_hours = TimeEntry.objects.filter(
                user=user,
                start_time__date__gte=start_date,
                start_time__date__lte=end_date,
                end_time__isnull=False
            ).aggregate(total=Sum('duration'))['total'] or Decimal('0')
            
            # Calculate utilization percentage
            utilization_percentage = 0
            if capacity_hours > 0:
                utilization_percentage = min(100, (actual_hours / capacity_hours) * 100)
            
            utilization_data.append({
                'user_id': str(user.id),
                'user_name': user.user.get_full_name(),
                'team_name': user.team.name if user.team else None,
                'capacity_hours': float(capacity_hours),
                'actual_hours': float(actual_hours),
                'utilization_percentage': round(float(utilization_percentage), 2),
                'working_days': working_days
            })
        
        return Response({
            'period': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat()
            },
            'utilization_data': utilization_data
        })

    @action(detail=False, methods=['get'])
    def billing(self, request):
        """Get billing report."""
        user_profile = request.user.profile
        
        # Get query parameters
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        project_ids = request.query_params.getlist('project_ids')
        client_ids = request.query_params.getlist('client_ids')
        
        # Set default date range if not provided
        if not start_date:
            start_date = (timezone.now() - timedelta(days=30)).date()
        else:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        
        if not end_date:
            end_date = timezone.now().date()
        else:
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        
        # Build queryset
        queryset = TimeEntry.objects.filter(
            project__organization=user_profile.organization,
            start_time__date__gte=start_date,
            start_time__date__lte=end_date,
            end_time__isnull=False,
            is_billable=True
        )
        
        # Apply filters
        if project_ids:
            queryset = queryset.filter(project_id__in=project_ids)
        if client_ids:
            queryset = queryset.filter(project__client_id__in=client_ids)
        
        # Get data by project
        project_data = []
        projects = Project.objects.filter(organization=user_profile.organization)
        if project_ids:
            projects = projects.filter(id__in=project_ids)
        
        total_revenue = Decimal('0')
        total_hours = Decimal('0')
        
        for project in projects:
            project_entries = queryset.filter(project=project)
            project_hours = project_entries.aggregate(total=Sum('duration'))['total'] or Decimal('0')
            project_revenue = project_hours * (project.hourly_rate or Decimal('0'))
            
            total_revenue += project_revenue
            total_hours += project_hours
            
            project_data.append({
                'project_id': str(project.id),
                'project_name': project.name,
                'client_id': str(project.client.id),
                'client_name': project.client.name,
                'hourly_rate': float(project.hourly_rate or 0),
                'total_hours': float(project_hours),
                'total_revenue': float(project_revenue)
            })
        
        # Get data by client
        client_data = []
        clients = set(project.client for project in projects)
        if client_ids:
            clients = [c for c in clients if str(c.id) in client_ids]
        
        for client in clients:
            client_projects = projects.filter(client=client)
            client_hours = Decimal('0')
            client_revenue = Decimal('0')
            
            for project in client_projects:
                project_entries = queryset.filter(project=project)
                project_hours = project_entries.aggregate(total=Sum('duration'))['total'] or Decimal('0')
                project_revenue = project_hours * (project.hourly_rate or Decimal('0'))
                
                client_hours += project_hours
                client_revenue += project_revenue
            
            client_data.append({
                'client_id': str(client.id),
                'client_name': client.name,
                'total_hours': float(client_hours),
                'total_revenue': float(client_revenue)
            })
        
        return Response({
            'summary': {
                'total_hours': float(total_hours),
                'total_revenue': float(total_revenue),
                'period': {
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat()
                }
            },
            'by_project': project_data,
            'by_client': client_data
        })

    @action(detail=False, methods=['get'])
    def export(self, request, format=None):
        """Export report data."""
        report_type = request.query_params.get('type', 'time')
        format_type = request.query_params.get('format', 'csv')
        
        if format_type == 'csv':
            return self._export_csv(request, report_type)
        elif format_type == 'pdf':
            return self._export_pdf(request, report_type)
        else:
            return Response({'error': 'Unsupported format'}, status=status.HTTP_400_BAD_REQUEST)

    def _export_csv(self, request, report_type):
        """Export data as CSV."""
        # Get report data based on type
        if report_type == 'time':
            response_data = self.time_summary(request).data
            filename = 'time_report.csv'
        elif report_type == 'utilization':
            response_data = self.utilization(request).data
            filename = 'utilization_report.csv'
        elif report_type == 'billing':
            response_data = self.billing(request).data
            filename = 'billing_report.csv'
        else:
            return Response({'error': 'Unsupported report type'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Create CSV response
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        writer = csv.writer(response)
        
        # Write summary data
        if 'summary' in response_data:
            writer.writerow(['Summary'])
            for key, value in response_data['summary'].items():
                if isinstance(value, dict):
                    writer.writerow([key, str(value)])
                else:
                    writer.writerow([key, str(value)])
            writer.writerow([])
        
        # Write detailed data
        if 'by_user' in response_data:
            writer.writerow(['By User'])
            writer.writerow(['User Name', 'Total Hours', 'Billable Hours', 'Non-Billable Hours', 'Utilization %'])
            for user_data in response_data['by_user']:
                writer.writerow([
                    user_data['user_name'],
                    user_data['total_hours'],
                    user_data['billable_hours'],
                    user_data['non_billable_hours'],
                    user_data.get('utilization_percentage', 0)
                ])
            writer.writerow([])
        
        if 'by_project' in response_data:
            writer.writerow(['By Project'])
            writer.writerow(['Project Name', 'Client Name', 'Total Hours', 'Billable Hours', 'Total Revenue'])
            for project_data in response_data['by_project']:
                writer.writerow([
                    project_data['project_name'],
                    project_data.get('client_name', ''),
                    project_data['total_hours'],
                    project_data['billable_hours'],
                    project_data.get('total_revenue', 0)
                ])
        
        return response

    def _export_pdf(self, request, report_type):
        """Export data as PDF."""
        # This would require reportlab implementation
        # For now, return a placeholder response
        return Response({'message': 'PDF export not implemented yet'}, status=status.HTTP_501_NOT_IMPLEMENTED)