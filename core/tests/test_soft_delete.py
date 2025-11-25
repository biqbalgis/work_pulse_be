from django.test import TestCase
from tags.models import Tag
from workspaces.models import Workspace
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()

class SoftDeleteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.workspace = Workspace.objects.create(name='Test Workspace', created_by=self.user)
        self.obj1 = Tag.objects.create(name="Tag 1", workspace=self.workspace)
        self.obj2 = Tag.objects.create(name="Tag 2", workspace=self.workspace)

    def test_soft_delete(self):
        """Test that delete() sets is_deleted=True and filters from default queryset"""
        # Initial state
        self.assertEqual(Tag.objects.count(), 2)
        
        # Soft delete obj1
        self.obj1.delete()
        
        # Verify it's gone from default manager
        self.assertEqual(Tag.objects.count(), 1)
        self.assertEqual(Tag.objects.first(), self.obj2)
        
        # Verify it still exists in DB
        self.obj1.refresh_from_db()
        self.assertTrue(self.obj1.is_deleted)
        self.assertIsNotNone(self.obj1.deleted_at)

    def test_all_objects_manager(self):
        """Test that all_objects manager returns deleted items"""
        self.obj1.delete()
        
        # Default manager excludes it
        self.assertEqual(Tag.objects.count(), 1)
        
        # All objects manager includes it
        self.assertEqual(Tag.all_objects.count(), 2)

    def test_restore(self):
        """Test that we can restore a soft-deleted item"""
        self.obj1.delete()
        self.assertTrue(self.obj1.is_deleted)
        
        # Restore
        self.obj1.is_deleted = False
        self.obj1.deleted_at = None
        self.obj1.save()
        
        # Verify it's back
        self.assertEqual(Tag.objects.count(), 2)
