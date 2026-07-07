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

        # 取得所有已發布的訓練設定（按時間正序，最新的在最後）
        result = sb.table("training_sets").select("*").eq(
            "company_id", company_id
        ).eq(
            "is_published", True
        ).eq(
            "is_active", True
        ).order(
            "created_at", desc=False
        ).execute()

        if not result.data:
            raise ValueError("無已發布的訓練設定")

        # 合併所有 PDF 的題目
        combined_questions = []
        combined_by_category = {}
        for row in result.data:
            qbc = row.get("questions_by_category") or {}
            for cat, qs in qbc.items():
                if cat not in combined_by_category:
                    combined_by_category[cat] = []
                combined_by_category[cat].extend(qs)
            combined_questions.extend(row.get("questions") or [])

        # 其他設定從最新那筆取
        latest = result.data[-1]
        all_filenames = "、".join(r.get("filename", "") for r in result.data)

        # 欄位映射（Supabase 欄位名 → session_state 鍵名）
        mapping = {
            "main_analysis":         latest.get("main_analysis"),
            "questions":             combined_questions,
            "analyzed_filename":     all_filenames,
            "product_name":          latest.get("product_name"),
            "product_benefits":      latest.get("product_benefits"),
            "target_audience":       latest.get("target_audience"),
            "published_questions":   latest.get("published_questions"),
            "customer_scenario":     latest.get("customer_scenario"),
            "task_published":        latest.get("is_published", False),
            "questions_by_category": combined_by_category,
        }
        for key, val in mapping.items():
            if key not in st.session_state and val is not None and val != [] and val != "":
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


def get_all_training_sets(company_id: str) -> list:
    """取得公司所有已發布的訓練集，供主管管理 PDF 列表使用"""
    try:
        sb = get_supabase()
        result = sb.table("training_sets").select(
            "id, filename, questions_by_category, created_at, is_active"
        ).eq("company_id", company_id).eq(
            "is_published", True
        ).order("created_at", desc=False).execute()
        return result.data if result.data else []
    except Exception as e:
        print(f"[Supabase警告] get_all_training_sets 失敗：{e}")
        return []


def delete_training_set(training_set_id: str) -> bool:
    """刪除指定的訓練集（單份 PDF）"""
    try:
        sb = get_supabase()
        sb.table("training_sets").delete().eq("id", training_set_id).execute()
        return True
    except Exception as e:
        print(f"[Supabase警告] delete_training_set 失敗：{e}")
        return False


def toggle_training_set_active(training_set_id: str, is_active: bool) -> bool:
    """切換指定訓練集的啟用/停用狀態"""
    try:
        sb = get_supabase()
        sb.table("training_sets").update(
            {"is_active": is_active}
        ).eq("id", training_set_id).execute()
        return True
    except Exception as e:
        print(f"[Supabase警告] toggle_training_set_active 失敗：{e}")
        return False


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


def get_company_by_access_code(access_code: str):
    """依 access_code 查詢公司，回傳 dict 或 None"""
    try:
        sb = get_supabase()
        result = sb.table("companies").select(
            "id, name, access_code, admin_password_hash"
        ).eq("access_code", access_code).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[Supabase警告] get_company_by_access_code 失敗：{e}")
        return None


def get_employee_by_username(username: str):
    """依 username 查詢員工帳號，回傳 dict（含 company_id、employee_name）或 None"""
    try:
        sb = get_supabase()
        result = sb.table("employees").select(
            "id, company_id, employee_name, username, password_hash"
        ).eq("username", username).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[Supabase警告] get_employee_by_username 失敗：{e}")
        return None


def get_company_name_by_id(company_id: str) -> str:
    """依 company_id 查詢公司名稱"""
    try:
        sb = get_supabase()
        result = sb.table("companies").select("name").eq("id", company_id).execute()
        return result.data[0]["name"] if result.data else ""
    except Exception as e:
        print(f"[Supabase警告] get_company_name_by_id 失敗：{e}")
        return ""


def set_company_credentials(company_id: str, access_code: str, admin_password_hash: str) -> bool:
    """設定（或覆寫）指定公司的登入代號與管理員密碼"""
    try:
        sb = get_supabase()
        sb.table("companies").update({
            "access_code": access_code,
            "admin_password_hash": admin_password_hash
        }).eq("id", company_id).execute()
        return True
    except Exception as e:
        print(f"[Supabase警告] set_company_credentials 失敗：{e}")
        return False


def create_employee_account(company_id: str, employee_name: str, username: str, password_hash: str) -> bool:
    """新增一位員工的登入帳號"""
    try:
        sb = get_supabase()
        sb.table("employees").insert({
            "company_id":    company_id,
            "employee_name": employee_name,
            "username":      username,
            "password_hash": password_hash,
        }).execute()
        return True
    except Exception as e:
        print(f"[Supabase警告] create_employee_account 失敗：{e}")
        return False


def list_all_companies() -> list:
    """列出所有公司，供平台管理頁面下拉選單使用"""
    try:
        sb = get_supabase()
        result = sb.table("companies").select("id, name, access_code").order("name").execute()
        return result.data if result.data else []
    except Exception as e:
        print(f"[Supabase警告] list_all_companies 失敗：{e}")
        return []
