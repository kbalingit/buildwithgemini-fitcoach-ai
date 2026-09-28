import os
from google.cloud import firestore

FIRESTORE_PROJECT_ID = "qwiklabs-gcp-02-3c8adf935961"

def seed_db():
    print(f"Connecting to Firestore with project ID: {FIRESTORE_PROJECT_ID}...")
    db = firestore.Client(project=FIRESTORE_PROJECT_ID)
    
    # 1. Seed workouts collection
    workouts = [
        {
            "id": "workout_hiit_01",
            "name": "Full Body HIIT Blast",
            "category": "HIIT",
            "duration_mins": 30,
            "calories_burned_est": 350,
            "difficulty": "Intermediate",
            "description": "High intensity interval training involving burpees, mountain climbers, and jump squats."
        },
        {
            "id": "workout_strength_01",
            "name": "Upper Body Power Hour",
            "category": "Strength",
            "duration_mins": 45,
            "calories_burned_est": 280,
            "difficulty": "Advanced",
            "description": "Dumbbell bench press, bent-over rows, shoulder presses, and bicep curls."
        },
        {
            "id": "workout_cardio_01",
            "name": "Morning Jog & Core Routine",
            "category": "Cardio",
            "duration_mins": 40,
            "calories_burned_est": 320,
            "difficulty": "Beginner",
            "description": "Moderate pace outdoor jog followed by plank variations and leg raises."
        },
        {
            "id": "workout_yoga_01",
            "name": "Vinyasa Flow & Recovery",
            "category": "Flexibility",
            "duration_mins": 30,
            "calories_burned_est": 150,
            "difficulty": "Beginner",
            "description": "Gentle stretching and mobility flow for active recovery days."
        }
    ]
    
    print("Seeding 'workouts' collection...")
    for w in workouts:
        doc_ref = db.collection("workouts").document(w["id"])
        doc_ref.set(w)
        print(f"  Added workout: {w['name']}")

    # 2. Seed sample daily_logs collection
    sample_log = {
        "id": "log_test_user_2026-09-28",
        "user_id": "test_user",
        "date": "2026-09-28",
        "meals": [
            {"name": "Oatmeal & Protein Shake", "calories": 450},
            {"name": "Grilled Chicken Salad", "calories": 550}
        ],
        "workouts": [
            {"name": "Full Body HIIT Blast", "duration_mins": 30, "calories_burned": 350}
        ],
        "calories_consumed": 1000,
        "calories_burned": 350,
        "net_calories": 650
    }
    
    print("Seeding 'daily_logs' collection...")
    db.collection("daily_logs").document(sample_log["id"]).set(sample_log)
    print(f"  Added daily log for user '{sample_log['user_id']}' on {sample_log['date']}")
    
    print("Firestore database seeded successfully!")

if __name__ == "__main__":
    seed_db()
