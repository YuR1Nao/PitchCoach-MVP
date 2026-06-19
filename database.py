import streamlit as st
import json
from supabase import create_client, Client
from config import (SUPABASE_URL, SUPABASE_KEY, SETTINGS_FILE, PERSIST_KEYS)


def get_supabase() -> Client:
    """建立並回傳 Supabase client 實例。"""
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def load_settings() -> None:
    """
    程式啟動時還原主管設定到 session_state。

    優先順序：
    1. Supabase training_sets（取 company 下最新一筆 is_published=True 的記錄）
    2. 若 Supabase 失敗，fallback 到 company_settings.json

    只還原 session_state 中尚未存在的鍵，避免覆蓋當次已操作的資料。
    """
    try:
        sb = get_supabase()

        # 取得當前登入公司的 company_id
        company = sb.table("companies").select("id").eq(
            "name", st.session_state.get("current_company", "")
        ).execute()
        if not company.data:
            raise ValueError("找不到公司記錄")
        company_id = company.data[0]["id"]

        # 取得最新一筆已發布的訓練設定
        result = sb.table("training_sets").select("*").eq(
            "company_id", company_id
        ).eq(
            "is_published", True
        ).order(
            "created_at", desc=True
        ).limit(1).execute()

        if not result.data:
            raise ValueError("無已發布的訓練設定")

        row = result.data[0]

        # 欄位映射（Supabase 欄位名 → session_state 鍵名）
        mapping = {
            "main_analysis":         row.get("main_analysis"),
            "questions":             row.get("questions"),
            "analyzed_filename":     row.get("filename"),
            "product_name":          row.get("product_name"),
            "product_benefits":      row.get("product_benefits"),
            "target_audience":       row.get("target_audience"),
            "published_questions":   row.get("published_questions"),
            "customer_scenario":     row.get("customer_scenario"),
            "task_published":        row.get("is_published", False),
            "questions_by_category": row.get("questions_by_category"),
        }
        for key, val in mapping.items():
            if key not in st.session_state and val is not None:
                st.session_state[key] = val

        print("[Supabase] load_settings 成功")

    except Exception as e:
        print(f"[Supabase警告] load_settings 失敗：{e}，嘗試從 JSON 備援讀取")
        # Fallback：從本機 company_settings.json 讀取
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                for key, val in data.items():
                    if key not in st.session_state:
                        st.session_state[key] = val
                print("[JSON] load_settings fallback 成功")
            except Exception as e:
                st.warning(f"⚠️ 本機設定載入失敗：{str(e)}，將使用預設值")


def get_or_create_company(name: str = "") -> str:
    """
    查詢 Supabase companies 資料表中是否已存在指定公司，
    存在則回傳 id；不存在則新增後回傳 id。
    失敗時靜默 print 警告並回傳空字串，不影響主流程。
    """
    try:
        sb = get_supabase()
        result = sb.table("companies").select("id").eq("name", name).execute()
        if result.data:
            return result.data[0]["id"]
        new = sb.table("companies").insert({"name": name}).execute()
        return new.data[0]["id"]
    except Exception as e:
        print(f"[Supabase警告] get_or_create_company 失敗：{e}")
        return ""


def save_settings() -> None:
    """
    將 PERSIST_KEYS 中有值的鍵寫入 company_settings.json。
    主管下次重整頁面後，load_settings() 會自動還原這些設定。
    同時嘗試將訓練集資料同步至 Supabase training_sets 資料表。
    """
    data = {k: st.session_state[k] for k in PERSIST_KEYS if k in st.session_state}
    SETTINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # 同步至 Supabase（失敗只 print 警告，不中斷主流程）
    try:
        sb = get_supabase()
        company_id = get_or_create_company(st.session_state.get("current_company", ""))
        if company_id:
            sb.table("training_sets").insert({
                "company_id":            company_id,
                "filename":              st.session_state.get("analyzed_filename", ""),
                "product_name":          st.session_state.get("product_name", ""),
                "main_analysis":         st.session_state.get("main_analysis", ""),
                "product_benefits":      st.session_state.get("product_benefits", ""),
                "target_audience":       st.session_state.get("target_audience", ""),
                "questions":             st.session_state.get("questions", []),
                "published_questions":   st.session_state.get("published_questions", []),
                "customer_scenario":     st.session_state.get("customer_scenario", ""),
                "questions_by_category": st.session_state.get("questions_by_category", {}),
                "is_published":          True
            }).execute()
            print("[Supabase] training_sets 儲存成功")
    except Exception as e:
        st.warning(f"⚠️ Supabase 儲存失敗：{str(e)}，設定已存入本機 JSON")
