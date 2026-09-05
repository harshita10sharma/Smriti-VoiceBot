"""Demo/test data.

Used by ``python -m smriti_voice --demo`` and by the integration tests so that
the personal-memory path can be exercised without a caregiver app.  Seeded rows
are marked ``source='seed'``: real deployments should never ship with them.
"""
from __future__ import annotations

from datetime import date, timedelta

from .models import (
    Appointment,
    DailyRoutine,
    FamilyMember,
    Game,
    Meal,
    Medicine,
    PersonalMemory,
    Provenance,
    User,
    Visitor,
)
from .repository import MemoryRepository

SEED = Provenance(source='seed', created_by='seed-script', verification_status='verified')


def seed_demo_user(repo: MemoryRepository, user_id: str = 'demo-user',
                   *, today: date | None = None) -> User:
    """Create the demo elder used throughout the documentation and tests."""
    reference = today or date.today()
    yesterday = reference - timedelta(days=1)

    user = User(user_id=user_id, display_name='Ravi Sharma', preferred_language='eng',
                location='Guwahati', latitude=26.1445, longitude=91.7362)
    repo.upsert_user(user)

    repo.add_family_member(FamilyMember(
        user_id=user_id, name='Bina', relation='daughter', phone='+919000000001',
        is_trusted_contact=True, is_primary_contact=True, lives_in='Shillong',
        notes='Calls every Sunday evening.', provenance=SEED))
    repo.add_family_member(FamilyMember(
        user_id=user_id, name='Rakesh', relation='son', phone='+919000000002',
        is_trusted_contact=True, lives_in='Guwahati',
        notes='Visits on weekends.', provenance=SEED))
    repo.add_family_member(FamilyMember(
        user_id=user_id, name='Meera', relation='granddaughter', lives_in='Shillong',
        notes='Studies in class 9.', provenance=SEED))

    for meal_type, description in (('breakfast', 'Poha'), ('lunch', 'Rice and dal'),
                                   ('dinner', 'Roti and sabzi')):
        repo.add_meal(Meal(user_id=user_id, meal_date=yesterday, meal_type=meal_type,
                           description=description, provenance=SEED))
    repo.add_meal(Meal(user_id=user_id, meal_date=reference, meal_type='breakfast',
                       description='Idli and sambar', provenance=SEED))

    repo.add_medicine(Medicine(user_id=user_id, name='Amlodipine', dosage='5 mg',
                               schedule_time='08:00', time_of_day='morning',
                               instructions='After breakfast', prescribed_by='Dr. Barua',
                               provenance=SEED))
    repo.add_medicine(Medicine(user_id=user_id, name='Metformin', dosage='500 mg',
                               schedule_time='20:00', time_of_day='night',
                               instructions='After dinner', prescribed_by='Dr. Barua',
                               provenance=SEED))

    repo.add_appointment(Appointment(user_id=user_id, title='Eye check-up',
                                     appointment_date=reference, appointment_time='16:00',
                                     location='City Hospital', with_person='Dr. Barua',
                                     provenance=SEED))

    for title, routine_time in (('Morning walk', '06:30'), ('Tea', '08:30'),
                                ('Afternoon rest', '14:00'), ('Evening prayer', '18:30')):
        repo.add_routine(DailyRoutine(user_id=user_id, title=title, routine_time=routine_time,
                                      provenance=SEED))

    repo.add_visitor(Visitor(user_id=user_id, name='Rakesh', relation='son',
                             visit_date=reference, visit_time='17:00', provenance=SEED))

    repo.add_memory(PersonalMemory(
        user_id=user_id, title="Bina's wedding",
        content='Bina was married in Shillong in 1998. The whole family travelled together by bus '
                'and it rained the entire day.',
        category='family', people='Bina', happened_on=date(1998, 11, 12), provenance=SEED))
    repo.add_memory(PersonalMemory(
        user_id=user_id, title='The garden',
        content='Ravi grows tomatoes, chillies and tulsi in the back garden every summer.',
        category='hobby', people='Ravi', provenance=SEED))

    for key, name, description in (
        ('memory_match', 'Memory Match', 'Match pairs of family photographs.'),
        ('word_recall', 'Word Recall', 'Remember and repeat a short list of words.'),
        ('number_order', 'Number Order', 'Put numbers back in the right order.'),
    ):
        repo.add_game(Game(user_id=user_id, game_key=key, display_name=name,
                           description=description))
    return user
