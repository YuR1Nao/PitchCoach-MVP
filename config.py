import os
import pathlib
from dotenv import load_dotenv
load_dotenv()

ADMIN_PASSWORD  = os.environ.get("ADMIN_PASSWORD", "admin123")
SUPER_ADMIN_PASSWORD = os.environ.get("SUPER_ADMIN_PASSWORD", "")
SUPABASE_URL    = os.environ.get("SUPABASE_URL")
SUPABASE_KEY    = os.environ.get("SUPABASE_KEY")
API_KEY         = os.environ.get("ANTHROPIC_API_KEY")
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY")

REQUIRED_SELECTION = 2

# AI 萃取題目：單次上傳（單一PDF）最多生成的題目總數，不分類別個別上限，
# 由AI依材料在各類別的豐富程度智能分配。若上傳材料的題目量很大（例如整份
# 90題的文件），建議拆分成多份文件分次上傳，每次都各自享有這個上限。
TOTAL_QUESTION_LIMIT = 30

CATEGORY_LABELS = {
    "cat_1_product":     "🔍 第一類：產品理解",
    "cat_2_price":       "💰 第二類：價格異議",
    "cat_3_trust":       "🛡️ 第三類：信任疑慮",
    "cat_4_competition": "⚔️ 第四類：競品比較",
    "cat_5_decision":    "🚪 第五類：決策障礙",
    "cat_org_trust":     "🏢 第六類：組織與商業模式疑慮",
    "cat_rules_info":    "📖 第七類：規則與資格說明",
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
