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
        owner = User(name="Zhanna", email="zhanna@cosmorent.kz", password_hash=get_password_hash("zhanna123"), role=RoleEnum.owner)
        dispatcher = User(name="Менеджер", email="manager@cosmorent.kz", phone="87762113673", password_hash=get_password_hash("manager123"), role=RoleEnum.dispatcher)
        dispatcher_max = User(name="Макс", email="maksim@cosmorent.kz", password_hash=get_password_hash("maksim123"), role=RoleEnum.admin)
        tech1 = User(name="Maxim T.", email="tech@cosmopolis.com", phone="+7 (701) 487-71-50", password_hash=get_password_hash("tech123"), role=RoleEnum.technician, specialties=["electrical", "plumbing", "heating", "structural", "appliance"])
        tech2 = User(name="Sarah L.", email="sarah@cosmopolis.com", phone="+7 (702) 555-56-78", password_hash=get_password_hash("tech123"), role=RoleEnum.technician, specialties=["electrical", "plumbing", "heating", "structural", "appliance"])
        agent = User(name="Агент", email="agent@cosmopolis.com", password_hash=get_password_hash("agent123"), role=RoleEnum.agent)

        db.add_all([admin, owner, dispatcher, dispatcher_max, tech1, tech2, agent])
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

        t1 = Tenant(name="Alisher Iglymov", phone="87762113673", apartment="4B", building_id=b1.id)
        t2 = Tenant(name="Maksim Kopochkin", phone="87014877150", apartment="4B", building_id=b1.id)
        t3 = Tenant(name="Zhanna S.", phone="87057777677", apartment="4B", building_id=b1.id)
        db.add_all([t1, t2, t3])
        db.commit()

        print("Database seeding completed successfully!")

    finally:
        db.close()

if __name__ == "__main__":
    seed_database()
