import streamlit as st
import json
from supabase import create_client, Client
from config import (SUPABASE_URL, SUPABASE_KEY)


def get_supabase() -> Client:
    """建立並回傳 Supabase client 實例。"""
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def load_settings() -> None:
    """
    程式啟動時還原主管設定到 session_state。

    只查詢當前登入公司（依 session_state 的 company_id）自己的 Supabase 資料。
    查無資料代表這是全新公司或尚未上傳教材，屬於正常狀態，直接維持空白，
    不會再 fallback 到任何本機快取檔案（避免不同公司的資料互相污染）。

    只還原 session_state 中尚未存在的鍵，避免覆蓋當次已操作的資料。
    """
    company_id = st.session_state.get("company_id", "")
    if not company_id:
        print("[Supabase警告] load_settings：session_state 尚無 company_id，略過")
        return

    try:
        sb = get_supabase()

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
            # 全新公司或尚未上傳教材，這是正常狀態，不是錯誤
            print("[Supabase] load_settings：此公司尚無已發布的訓練設定（正常）")
            return

        combined_questions = []
        combined_by_category = {}
        for row in result.data:
            qbc = row.get("questions_by_category") or {}
            for cat, qs in qbc.items():
                if cat not in combined_by_category:
                    combined_by_category[cat] = []
                combined_by_category[cat].extend(qs)
            combined_questions.extend(row.get("questions") or [])

        latest = result.data[-1]
        all_filenames = "、".join(r.get("filename", "") for r in result.data)

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
        # 這裡只會是真正的連線／查詢錯誤（例如 Supabase 暫時連不上），
        # 不再 fallback 到本機 JSON，避免不同公司的資料互相污染
        print(f"[Supabase警告] load_settings 失敗：{e}")
        st.warning(f"⚠️ 讀取公司設定時發生問題，部分資料可能需要重新整理頁面：{str(e)}")


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
    將本次上傳/發布的訓練集資料同步至 Supabase training_sets 資料表。
    """
    company_id = st.session_state.get("company_id", "")
    if not company_id:
        st.warning("⚠️ 尚未取得公司身份，無法儲存設定")
        return

    try:
        sb = get_supabase()
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
        st.error(f"⚠️ Supabase 儲存失敗：{str(e)}，請重新嘗試發布")


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
        st.error(f"⚠️ 設定公司帳密失敗：{str(e)}")
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
        st.error(f"⚠️ 建立員工帳號失敗：{str(e)}")
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
