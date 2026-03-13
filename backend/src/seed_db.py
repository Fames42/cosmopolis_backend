from src.database import SessionLocal, engine
from src.models import Base, User, RoleEnum, Building, Tenant, Ticket, TicketStatusEnum, TicketNote, TechnicianSchedule
from src.auth import get_password_hash
from datetime import datetime, timezone, timedelta
import uuid

def seed_database():
    print("Recreating database tables...")
    # For development, drop and recreate to have clean seed data
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        print("Seeding Users...")
        admin = User(name="System Admin", email="admin@cosmopolis.com", password_hash=get_password_hash("admin123"), role=RoleEnum.admin)
        owner = User(name="Test Owner", email="owner@cosmopolis.com", password_hash=get_password_hash("owner123"), role=RoleEnum.owner)
        dispatcher = User(name="Test Dispatcher", email="dispatcher@cosmopolis.com", password_hash=get_password_hash("dispatcher123"), role=RoleEnum.dispatcher)
        tech1 = User(name="Mike T.", email="tech@cosmopolis.com", phone="+7 (701) 555-12-34", password_hash=get_password_hash("tech123"), role=RoleEnum.technician, specialties=["plumbing", "heating", "appliance"])
        tech2 = User(name="Sarah L.", email="sarah@cosmopolis.com", phone="+7 (702) 555-56-78", password_hash=get_password_hash("tech123"), role=RoleEnum.technician, specialties=["electrical", "structural", "appliance"])
        agent = User(name="Test Agent", email="agent@cosmopolis.com", password_hash=get_password_hash("agent123"), role=RoleEnum.agent)

        db.add_all([admin, owner, dispatcher, tech1, tech2, agent])
        db.commit()

        print("Seeding Technician Schedules...")
        # Mike: Mon-Fri 09:00-18:00
        for day in range(5):
            db.add(TechnicianSchedule(technician_id=tech1.id, day_of_week=day, start_time="09:00", end_time="18:00"))
        # Sarah: Mon/Wed/Fri 10:00-19:00, Tue/Thu 08:00-16:00
        for day in [0, 2, 4]:
            db.add(TechnicianSchedule(technician_id=tech2.id, day_of_week=day, start_time="10:00", end_time="19:00"))
        for day in [1, 3]:
            db.add(TechnicianSchedule(technician_id=tech2.id, day_of_week=day, start_time="08:00", end_time="16:00"))
        db.commit()

        print("Seeding Buildings and Tenants...")
        b1 = Building(name="Building A", address="123 Cosmopolis Way", owner_id=owner.id)
        b2 = Building(name="Building B", address="125 Cosmopolis Way", owner_id=owner.id)
        b3 = Building(name="Building C", address="127 Cosmopolis Way", owner_id=owner.id)
        
        db.add_all([b1, b2, b3])
        db.commit()

        t1 = Tenant(name="John Smith", phone="+1 (234) 567-890", apartment="4B", building_id=b1.id)
        t2 = Tenant(name="Alice Johnson", phone="+1 (234) 567-891", apartment="2A", building_id=b1.id)
        t3 = Tenant(name="Bob Williams", phone="+1 (234) 567-892", apartment="3C", building_id=b2.id)
        t4 = Tenant(name="Emma Davis", phone="+1 (234) 567-893", apartment="2B", building_id=b3.id)
        
        db.add_all([t1, t2, t3, t4])
        db.commit()

        print("Seeding Tickets...")
        now = datetime.now(timezone.utc)
        
        ticket1 = Ticket(
            ticket_number="T-1024",
            tenant_id=t1.id,
            category="Plumbing",
            urgency="EMERGENCY",
            description="Water is continuously leaking from the bathroom ceiling. It's causing damage to the floor.",
            assigned_to=tech1.id,
            status=TicketStatusEnum.assigned,
            scheduled_time=now + timedelta(hours=2),
            created_at=now - timedelta(hours=1)
        )
        
        ticket2 = Ticket(
            ticket_number="T-1025",
            tenant_id=t4.id,
            category="Plumbing",
            urgency="HIGH",
            description="Kitchen sink is clogged and water won't drain.",
            assigned_to=tech1.id,
            status=TicketStatusEnum.assigned,
            scheduled_time=now + timedelta(hours=4),
            created_at=now - timedelta(days=1)
        )

        ticket3 = Ticket(
            ticket_number="T-1026",
            tenant_id=t2.id,
            category="Electrical",
            urgency="MEDIUM",
            description="Living room lights keep flickering when turned on.",
            assigned_to=tech2.id,
            status=TicketStatusEnum.scheduled,
            scheduled_time=now + timedelta(days=1, hours=2),
            created_at=now - timedelta(days=2)
        )
        
        ticket4 = Ticket(
            ticket_number="T-1027",
            tenant_id=t3.id,
            category="HVAC",
            urgency="HIGH",
            description="AC is not blowing cold air. It's very hot inside.",
            status=TicketStatusEnum.new,
            created_at=now - timedelta(minutes=30)
        )
        
        ticket5 = Ticket(
            ticket_number="T-1028",
            tenant_id=t1.id,
            category="Appliance",
            urgency="LOW",
            description="Dishwasher is making a weird noise during the wash cycle.",
            assigned_to=tech1.id,
            status=TicketStatusEnum.done,
            scheduled_time=now - timedelta(days=1),
            created_at=now - timedelta(days=3)
        )

        db.add_all([ticket1, ticket2, ticket3, ticket4, ticket5])
        db.commit()

        print("Seeding Notes...")
        note1 = TicketNote(
            ticket_id=ticket1.id,
            author_id=dispatcher.id,
            text="Tenant called in a panic. Instructed them to turn off main water valve.",
            created_at=now - timedelta(minutes=45)
        )
        note2 = TicketNote(
            ticket_id=ticket1.id,
            author_id=tech1.id,
            text="I'll grab some spare pipe fittings and head over immediately.",
            created_at=now - timedelta(minutes=15)
        )
        note3 = TicketNote(
            ticket_id=ticket2.id,
            author_id=tech1.id,
            text="Picked up snake tool from warehouse.",
            created_at=now - timedelta(minutes=5)
        )
        
        db.add_all([note1, note2, note3])
        db.commit()

        print("Database seeding completed successfully!")
            
    finally:
        db.close()

if __name__ == "__main__":
    seed_database()
