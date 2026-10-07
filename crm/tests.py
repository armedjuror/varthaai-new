from django.test import TestCase

from accounts.models import AdminUser
from core.models import Brand
from crm import services as crm_services
from crm.models import B2BActivity, B2BCompany


class VisitLoggingTests(TestCase):
    """log_visit() must mirror a field visit as a B2BActivity with
    structured purpose/outcome, and nudge a still-untouched lead forward a
    stage — but never regress or skip past where an admin/order already put
    it, and never bump the very company the visit itself created."""

    def setUp(self):
        self.brand = Brand.objects.create(name='Varthaai Test')
        self.user = AdminUser.objects.create_user(
            username='saad', password='pw123456', name='Saad', role=AdminUser.Role.STAFF,
        )
        self.company = B2BCompany.objects.create(
            brand=self.brand, company_name='ABC Mart', stage=B2BCompany.Stage.LEAD,
        )

    def test_logs_structured_activity(self):
        activity = crm_services.log_visit(
            self.user, self.company, 'retarget', 'follow_up', notes='left pamphlet',
        )
        self.assertEqual(activity.type, B2BActivity.Type.VISIT)
        self.assertEqual(activity.visit_purpose, 'retarget')
        self.assertEqual(activity.visit_outcome, 'follow_up')
        self.assertEqual(activity.admin_user_id, self.user.id)
        self.assertIn('Retarget', activity.subject)
        self.assertEqual(activity.description, 'left pamphlet')

    def test_retarget_visit_bumps_lead_to_contacted(self):
        crm_services.log_visit(self.user, self.company, 'retarget', 'none')

        self.company.refresh_from_db()
        self.assertEqual(self.company.stage, B2BCompany.Stage.CONTACTED)
        stage_activity = B2BActivity.objects.filter(
            company=self.company, type=B2BActivity.Type.STAGE_CHANGE,
        ).first()
        self.assertIsNotNone(stage_activity)
        self.assertEqual(stage_activity.old_stage, 'lead')
        self.assertEqual(stage_activity.new_stage, 'contacted')

    def test_new_lead_visit_does_not_bump_its_own_company(self):
        crm_services.log_visit(self.user, self.company, 'new_lead', 'none')

        self.company.refresh_from_db()
        self.assertEqual(self.company.stage, B2BCompany.Stage.LEAD)
        self.assertFalse(
            B2BActivity.objects.filter(company=self.company, type=B2BActivity.Type.STAGE_CHANGE).exists(),
        )

    def test_visit_does_not_regress_a_later_stage(self):
        self.company.stage = B2BCompany.Stage.NEGOTIATION
        self.company.save(update_fields=['stage'])

        crm_services.log_visit(self.user, self.company, 'retarget', 'none')

        self.company.refresh_from_db()
        self.assertEqual(self.company.stage, B2BCompany.Stage.NEGOTIATION)
