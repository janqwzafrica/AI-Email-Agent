from app import app, db
from models import ContactList

with app.app_context():
    lists = ContactList.query.all()
    print("count:", len(lists))
    for l in lists:
        print("id:", l.id, "| brevo_list_id:", l.brevo_list_id, "| name:", l.name)