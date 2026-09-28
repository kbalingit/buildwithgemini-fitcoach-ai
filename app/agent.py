# ruff: noqa
import datetime
import json
import os
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
from pathlib import Path
from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents.callback_context import CallbackContext
from google.adk.code_executors.agent_engine_sandbox_code_executor import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from .a2ui_utils import a2ui_callback

FIRESTORE_PROJECT_ID = "qwiklabs-gcp-02-3c8adf935961"
GCS_BUCKET_NAME = "fitcoach-ai-public-qwiklabs-gcp-02-3c8adf935961"
DEPLOYMENT_METADATA_PATH = Path(__file__).parent.parent / "deployment_metadata.json"


def _get_agent_engine_resource_name() -> str:
    if DEPLOYMENT_METADATA_PATH.exists():
        try:
            with open(DEPLOYMENT_METADATA_PATH, "r") as f:
                data = json.load(f)
                resource_name = data.get("remote_agent_runtime_id") or data.get("agent_engine_resource_name")
                if resource_name:
                    return resource_name
        except Exception:
            pass
    return "projects/979955230447/locations/us-east1/reasoningEngines/6601843846143803392"


def search_recipe_ideas(query: str) -> str:
    """Searches for healthy recipe ideas and ingredients using TheMealDB public API.

    Args:
        query: Main ingredient or meal keyword (e.g. 'chicken', 'salmon', 'salad', 'oatmeal').

    Returns:
        Formatted summary of recipe ideas with key ingredients.
    """
    api_key = os.getenv("THEMEALDB_API_KEY", "1")
    url = f"https://www.themealdb.com/api/json/v1/{api_key}/search.php?s={urllib.parse.quote(query)}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FitCoachAI/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))

        meals = data.get("meals")
        if not meals:
            return f"No recipe ideas found for '{query}'."

        results = []
        for meal in meals[:3]:
            ingredients = []
            for i in range(1, 21):
                ing = meal.get(f"strIngredient{i}")
                meas = meal.get(f"strMeasure{i}")
                if ing and ing.strip():
                    meas_str = f"{meas.strip()} " if (meas and meas.strip()) else ""
                    ingredients.append(f"{meas_str}{ing.strip()}")

            ing_str = ", ".join(ingredients[:6])
            results.append(
                f"- {meal.get('strMeal')} [{meal.get('strCategory', 'General')} | {meal.get('strArea', 'International')}]:\n"
                f"  Ingredients: {ing_str}...\n"
                f"  Image: {meal.get('strMealThumb')}"
            )

        return f"Recipe Ideas for '{query}':\n" + "\n".join(results)
    except Exception as e:
        return f"Error fetching recipes for '{query}': {str(e)}"


def get_workouts(category: str = "") -> str:
    """Retrieves workout routines from the Firestore database catalog.

    Args:
        category: Optional category filter (e.g. 'HIIT', 'Strength', 'Cardio', 'Flexibility').

    Returns:
        A summary string listing available workout routines.
    """
    db = firestore.Client(project=FIRESTORE_PROJECT_ID)
    docs = db.collection("workouts").stream()
    
    results = []
    for doc in docs:
        data = doc.to_dict()
        if category and category.lower() not in data.get("category", "").lower():
            continue
        results.append(
            f"- {data.get('name')} [{data.get('category')}]: {data.get('duration_mins')} mins, ~{data.get('calories_burned_est')} kcal burned. ({data.get('description')})"
        )
    
    if not results:
        return f"No workouts found matching category '{category}'."
    
    return "Available Workouts:\n" + "\n".join(results)


def log_activity(user_id: str, date: str, item_name: str, item_type: str, calories: int) -> str:
    """Logs a meal or a workout activity into the Firestore daily logs collection.

    Args:
        user_id: User identifier (e.g. 'test_user').
        date: Date string in YYYY-MM-DD format.
        item_name: Name of the meal or workout (e.g. 'Protein Shake' or 'Morning Jog').
        item_type: Type of item, either 'meal' (calories consumed) or 'workout' (calories burned).
        calories: Number of calories associated with this item.

    Returns:
        Status message confirming the activity log.
    """
    db = firestore.Client(project=FIRESTORE_PROJECT_ID)
    doc_id = f"log_{user_id}_{date}"
    doc_ref = db.collection("daily_logs").document(doc_id)
    doc = doc_ref.get()

    if doc.exists:
        data = doc.to_dict()
    else:
        data = {
            "id": doc_id,
            "user_id": user_id,
            "date": date,
            "meals": [],
            "workouts": [],
            "calories_consumed": 0,
            "calories_burned": 0,
            "net_calories": 0,
        }

    if item_type.lower() == "meal":
        data.setdefault("meals", []).append({"name": item_name, "calories": calories})
        data["calories_consumed"] = data.get("calories_consumed", 0) + calories
    elif item_type.lower() == "workout":
        data.setdefault("workouts", []).append({"name": item_name, "calories_burned": calories})
        data["calories_burned"] = data.get("calories_burned", 0) + calories
    else:
        return f"Invalid item_type '{item_type}'. Must be 'meal' or 'workout'."

    data["net_calories"] = data["calories_consumed"] - data["calories_burned"]
    doc_ref.set(data)

    return (
        f"Logged {item_type} '{item_name}' ({calories} kcal) for {user_id} on {date}. "
        f"New daily totals -> Consumed: {data['calories_consumed']} kcal, "
        f"Burned: {data['calories_burned']} kcal, Net: {data['net_calories']} kcal."
    )


def get_daily_summary(user_id: str, date: str) -> str:
    """Gets the daily fitness and nutrition summary log for a user from Firestore.

    Args:
        user_id: User identifier (e.g. 'test_user').
        date: Date string in YYYY-MM-DD format.

    Returns:
        A detailed summary of meals, workouts, calories consumed, burned, and net energy balance.
    """
    db = firestore.Client(project=FIRESTORE_PROJECT_ID)
    doc_id = f"log_{user_id}_{date}"
    doc = db.collection("daily_logs").document(doc_id).get()

    if not doc.exists:
        return f"No daily log found for user '{user_id}' on {date}."

    data = doc.to_dict()
    meals_str = ", ".join([f"{m['name']} ({m['calories']} kcal)" for m in data.get("meals", [])]) or "None"
    workouts_str = ", ".join([f"{w['name']} ({w.get('calories_burned', w.get('calories', 0))} kcal)" for w in data.get("workouts", [])]) or "None"

    return (
        f"Daily Summary for {user_id} on {date}:\n"
        f"- Meals: {meals_str}\n"
        f"- Workouts: {workouts_str}\n"
        f"- Total Consumed: {data.get('calories_consumed', 0)} kcal\n"
        f"- Total Burned: {data.get('calories_burned', 0)} kcal\n"
        f"- Net Calories: {data.get('net_calories', 0)} kcal"
    )


def calculate_macronutrients(
    weight_lbs: float,
    height_inches: float,
    age: int,
    gender: str = "male",
    activity_level: str = "moderate",
    goal: str = "maintenance",
) -> str:
    """Calculates BMR, TDEE, daily target calories, and macronutrient distribution (protein, carbs, fats).

    Args:
        weight_lbs: Body weight in pounds.
        height_inches: Height in inches.
        age: Age in years.
        gender: 'male' or 'female'.
        activity_level: 'sedentary', 'light', 'moderate', or 'active'.
        goal: 'weight_loss' (-500 kcal), 'maintenance' (0 kcal), or 'muscle_gain' (+300 kcal).

    Returns:
        A string summarizing BMR, TDEE, daily target calories, and macro breakdown in grams.
    """
    weight_kg = weight_lbs * 0.453592
    height_cm = height_inches * 2.54

    if gender.lower() == "female":
        bmr = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) - 161
    else:
        bmr = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) + 5

    activity_multipliers = {
        "sedentary": 1.2,
        "light": 1.375,
        "moderate": 1.55,
        "active": 1.725,
    }
    mult = activity_multipliers.get(activity_level.lower(), 1.55)
    tdee = bmr * mult

    goal_adjustments = {
        "weight_loss": -500,
        "maintenance": 0,
        "muscle_gain": 300,
    }
    adj = goal_adjustments.get(goal.lower(), 0)
    target_calories = round(tdee + adj)

    protein_g = round((target_calories * 0.30) / 4)
    carbs_g = round((target_calories * 0.40) / 4)
    fat_g = round((target_calories * 0.30) / 9)

    return (
        f"Nutritional Target & Macro Plan ({goal.replace('_', ' ').title()}):\n"
        f"- BMR (Basal Metabolic Rate): {round(bmr)} kcal\n"
        f"- TDEE (Daily Burn): {round(tdee)} kcal\n"
        f"- Target Daily Calories: {target_calories} kcal\n"
        f"- Recommended Daily Macros: Protein {protein_g}g | Carbs {carbs_g}g | Fat {fat_g}g"
    )


def estimate_workout_calories(exercise_type: str, duration_mins: int, weight_lbs: float) -> str:
    """Estimates calories burned for a workout based on MET (Metabolic Equivalent) and body weight.

    Args:
        exercise_type: Type of exercise (e.g., 'running', 'swimming', 'cycling', 'weightlifting', 'walking', 'hiit', 'yoga').
        duration_mins: Duration of the workout in minutes.
        weight_lbs: Body weight in pounds.

    Returns:
        Summary of estimated calories burned.
    """
    weight_kg = weight_lbs * 0.453592
    
    met_values = {
        "running": 9.8,
        "cycling": 7.5,
        "swimming": 8.0,
        "weightlifting": 5.0,
        "hiit": 8.0,
        "walking": 3.5,
        "yoga": 3.0,
    }
    
    exercise_key = exercise_type.lower().strip()
    met = met_values.get(exercise_key, 6.0)
    
    calories_burned = round((met * 3.5 * weight_kg / 200) * duration_mins)
    
    return (
        f"Estimated Burn for {exercise_type.title()} ({duration_mins} mins, {weight_lbs} lbs):\n"
        f"- MET Level: {met}\n"
        f"- Estimated Calories Burned: ~{calories_burned} kcal"
    )


async def generate_item_image(tool_context: ToolContext, prompt: str) -> str:
    """Generates an image for a meal, workout, or fitness item using gemini-3.1-flash-lite-image in global region,
    saves it as an ADK session artifact, and uploads it to public Cloud Storage.

    Args:
        prompt: Description of the meal or workout image to generate (e.g. 'A plate of grilled chicken with avocado').

    Returns:
        The public HTTPS URL of the uploaded image in Cloud Storage.
    """
    client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT_ID, location="global")
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-image",
        contents=[prompt],
        config=types.GenerateContentConfig(
            response_modalities=[types.Modality.IMAGE],
        ),
    )

    image_bytes = None
    mime_type = "image/jpeg"
    for part in response.candidates[0].content.parts:
        if part.inline_data:
            image_bytes = part.inline_data.data
            mime_type = part.inline_data.mime_type or "image/jpeg"
            break

    if not image_bytes:
        return f"Failed to generate image for prompt: '{prompt}'."

    sanitized_name = "".join(c if c.isalnum() else "_" for c in prompt[:20].lower()).strip("_")
    filename = f"{sanitized_name}_{int(datetime.datetime.now().timestamp())}.jpg"

    # (1) Save with tool_context.save_artifact so it shows in Playground Artifacts panel
    artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    await tool_context.save_artifact(filename, artifact_part)

    # (2) Upload to public Cloud Storage bucket and return public https URL
    storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(filename)
    blob.upload_from_string(image_bytes, content_type=mime_type)

    public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{filename}"
    return public_url


async def generate_memories_callback(callback_context: CallbackContext):
    """Sends completed session turns to Vertex AI Memory Bank for long-term extraction."""
    try:
        await callback_context.add_session_to_memory()
    except Exception as e:
        print(f"Memory callback notice: {e}")
    return None


def memory_bank_service_builder():
    """Builds VertexAiMemoryBankService instance targeting the deployed Agent Engine Memory Bank."""
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT_ID,
        location="us-east1",
        agent_engine_id="6601843846143803392",
    )


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are FitCoach AI, an intelligent fitness and nutrition coach. "
        "Continuously remember and recall across session conversations:\n"
        "1. All user allergies, intolerances, and dietary restrictions (e.g. peanuts, shellfish, gluten, lactose).\n"
        "2. Past workouts done (exercise names, duration, intensity, estimated calories burned).\n"
        "3. Past food and meals logged (meal names, calories, macros).\n"
        "4. Fitness and caloric progress over the past 7 days.\n\n"
        "Always cross-reference preloaded memories before recommending meals, recipe ideas, or workout plans. "
        "Use your tools to query workout routines, log activities to Firestore, calculate macros/TDEE, "
        "estimate workout calorie burn, search healthy recipes via live public API, generate item images, "
        "and execute Python code in the sandbox for progress calculations."
    ),
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=AgentEngineSandboxCodeExecutor(
        agent_engine_resource_name=_get_agent_engine_resource_name(),
    ),
    tools=[
        PreloadMemoryTool(),
        get_workouts,
        log_activity,
        get_daily_summary,
        calculate_macronutrients,
        estimate_workout_calories,
        search_recipe_ideas,
        generate_item_image,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
