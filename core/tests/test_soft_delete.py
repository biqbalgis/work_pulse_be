from django.test import TestCase
from core.models import SoftDeleteModel
from django.db import models
from django.utils import timezone

# Create a dummy model for testing
class TestModel(SoftDeleteModel):
    name = models.CharField(max_length=100)
    
    class Meta:
        app_label = 'core'

class SoftDeleteTests(TestCase):
    def setUp(self):
        self.obj1 = TestModel.objects.create(name="Object 1")
        self.obj2 = TestModel.objects.create(name="Object 2")

    def test_soft_delete(self):
        """Test that delete() sets is_deleted=True and filters from default queryset"""
        # Initial state
        self.assertEqual(TestModel.objects.count(), 2)
        
        # Soft delete obj1
        self.obj1.delete()
        
        # Verify it's gone from default manager
        self.assertEqual(TestModel.objects.count(), 1)
        self.assertEqual(TestModel.objects.first(), self.obj2)
        
        # Verify it still exists in DB
        self.obj1.refresh_from_db()
        self.assertTrue(self.obj1.is_deleted)
        self.assertIsNotNone(self.obj1.deleted_at)

    def test_all_objects_manager(self):
        """Test that all_objects manager returns deleted items"""
        self.obj1.delete()
        
        # Default manager excludes it
        self.assertEqual(TestModel.objects.count(), 1)
        
        # All objects manager includes it
        self.assertEqual(TestModel.all_objects.count(), 2)

    def test_restore(self):
        """Test that we can restore a soft-deleted item"""
        self.obj1.delete()
        self.assertTrue(self.obj1.is_deleted)
        
        # Restore
        self.obj1.is_deleted = False
        self.obj1.deleted_at = None
        self.obj1.save()
        
        # Verify it's back
        self.assertEqual(TestModel.objects.count(), 2)
