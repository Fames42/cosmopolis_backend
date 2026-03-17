from src.database import SessionLocal, engine
from src.models import Base, User, RoleEnum
from src.auth import get_password_hash


def seed_database():
    print("Recreating database tables...")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        print("Seeding Users...")
        alisher = User(name="Alisher", email="alisher@cosmorent.kz", password_hash=get_password_hash("alisher123"), role=RoleEnum.admin)
        maxim = User(name="Maxim", email="maxim@cosmorent.kz", password_hash=get_password_hash("maxim123"), role=RoleEnum.admin)
        zhanna = User(name="Zhanna", email="zhanna@cosmorent.kz", password_hash=get_password_hash("zhanna123"), role=RoleEnum.admin)

        db.add_all([alisher, maxim, zhanna])
        db.commit()

        print("Database seeding completed successfully!")

    finally:
        db.close()

if __name__ == "__main__":
    seed_database()
