import os
import pathlib
from dotenv import load_dotenv
load_dotenv()

ADMIN_PASSWORD  = os.environ.get("ADMIN_PASSWORD", "admin123")
SUPABASE_URL    = os.environ.get("SUPABASE_URL")
SUPABASE_KEY    = os.environ.get("SUPABASE_KEY")
API_KEY         = os.environ.get("ANTHROPIC_API_KEY")
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY")

REQUIRED_SELECTION = 2

CATEGORY_LABELS = {
    "cat_1_product":     "🔍 第一類：產品理解",
    "cat_2_price":       "💰 第二類：價格異議",
    "cat_3_trust":       "🛡️ 第三類：信任疑慮",
    "cat_4_competition": "⚔️ 第四類：競品比較",
    "cat_5_decision":    "🚪 第五類：決策障礙",
}

EDGE_TTS_VOICE = "zh-TW-HsiaoChenNeural"
EDGE_TTS_RATE  = "+20%"
SETTINGS_FILE  = pathlib.Path("company_settings.json")

PERSIST_KEYS = [
    "main_analysis", "questions", "analyzed_filename",
    "product_name", "product_benefits", "target_audience",
    "published_questions", "task_published", "customer_scenario",
    "is_completed", "evaluation_report", "question_mode",
    "questions_by_category",
]
