from __future__ import annotations
import base64, logging, re
from typing import Any, Dict, List, Optional
from urllib.parse import quote
import requests

logger = logging.getLogger(__name__)

class OneDriveConnector:
    """Microsoft Graph connector for governed Excel-in-OneDrive workflows."""
    GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
    READ_SCOPE = "Files.Read"
    READ_WRITE_SCOPE = "Files.ReadWrite"
    _EXCEL_EXTENSIONS = {".xlsx", ".xlsm"}

    def __init__(self, *, access_token: Optional[str] = None,
                 credentials: Any = None, timeout_seconds: int = 30) -> None:
        self.access_token = str(access_token or "").strip()
        self.credentials = credentials
        self.timeout_seconds = max(5, min(int(timeout_seconds), 120))
        logger.warning("ONEDRIVE CONNECTOR INIT token_provided=%s credentials_provided=%s",
                       bool(self.access_token), credentials is not None)

    def _get_access_token(self) -> str:
        if self.access_token:
            return self.access_token
        if isinstance(self.credentials, dict):
            token = self.credentials.get("access_token") or self.credentials.get("token")
            if token:
                return str(token)
        token = getattr(self.credentials, "token", None) if self.credentials else None
        if token:
            return str(token)
        raise RuntimeError("Microsoft OAuth credentials are required for OneDrive.")

    def _headers(self, session_id: Optional[str] = None) -> Dict[str, str]:
        h = {"Accept":"application/json","Content-Type":"application/json",
             "Authorization":f"Bearer {self._get_access_token()}"}
        if session_id:
            h["workbook-session-id"] = session_id
        return h

    @staticmethod
    def _raise_for_graph_error(response: requests.Response, action: str) -> None:
        if response.ok:
            return
        try:
            err = (response.json().get("error") or {})
            detail = str(err.get("message") or err.get("code") or "")
        except Exception:
            detail = (response.text or "").strip()
        if response.status_code == 401:
            raise RuntimeError(f"Microsoft Graph authentication failed during {action}: {detail}")
        if response.status_code == 403:
            raise RuntimeError(f"Microsoft Graph request rejected during {action}: {detail}")
        if response.status_code == 404:
            raise RuntimeError(f"OneDrive/Excel resource not found during {action}: {detail}")
        if response.status_code == 429:
            raise RuntimeError(f"Microsoft Graph throttled the request during {action}.")
        raise RuntimeError(f"Microsoft Graph {action} failed with HTTP {response.status_code}: {detail}")

    @staticmethod
    def _sharing_token(sharing_url: str) -> str:
        raw = str(sharing_url or "").strip()
        if not raw.startswith(("https://","http://")):
            raise ValueError("A valid OneDrive/SharePoint sharing URL is required.")
        return "u!" + base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    @classmethod
    def _validate_excel_filename(cls, name: str) -> None:
        name = str(name or "").strip().lower()
        ext = name[name.rfind("."):] if "." in name else ""
        if ext not in cls._EXCEL_EXTENSIONS:
            raise ValueError("OneDrive connector V1 supports .xlsx/.xlsm workbooks only.")

    def resolve_workbook(self, *, sharing_url: str) -> Dict[str, Any]:
        url = f"{self.GRAPH_API_BASE}/shares/{self._sharing_token(sharing_url)}/driveItem"
        r = requests.get(url, headers=self._headers(), timeout=self.timeout_seconds)
        self._raise_for_graph_error(r, "resolve OneDrive sharing URL")
        item = r.json()
        drive_id = str((item.get("parentReference") or {}).get("driveId") or "").strip()
        item_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not drive_id or not item_id:
            raise RuntimeError("Graph did not return drive_id/item_id.")
        self._validate_excel_filename(name)
        return {"drive_id":drive_id,"item_id":item_id,"name":name,
                "web_url":item.get("webUrl"),"size":item.get("size")}

    def _base(self, drive_id: str, item_id: str) -> str:
        drive_id, item_id = str(drive_id or "").strip(), str(item_id or "").strip()
        if not drive_id or not item_id:
            raise ValueError("drive_id and item_id are required.")
        return f"{self.GRAPH_API_BASE}/drives/{quote(drive_id,safe='')}/items/{quote(item_id,safe='')}"

    def test_connection(self, *, drive_id: str, item_id: str) -> Dict[str, Any]:
        base = self._base(drive_id,item_id)
        r = requests.get(base, headers=self._headers(), timeout=self.timeout_seconds)
        self._raise_for_graph_error(r,"load OneDrive workbook metadata")
        item=r.json(); self._validate_excel_filename(str(item.get("name") or ""))
        sheets=self.list_sheets(drive_id=drive_id,item_id=item_id)
        return {"success":True,"drive_id":drive_id,"item_id":item_id,
                "workbook_name":item.get("name"),"worksheet_count":len(sheets),
                "message":"OneDrive Excel connection successful."}

    def list_sheets(self, *, drive_id: str, item_id: str) -> List[Dict[str,Any]]:
        r=requests.get(f"{self._base(drive_id,item_id)}/workbook/worksheets",
                       headers=self._headers(),timeout=self.timeout_seconds)
        self._raise_for_graph_error(r,"list Excel worksheets")
        return [{"sheet_id":s.get("id"),"title":s.get("name"),
                 "index":s.get("position",i),"visibility":s.get("visibility"),
                 "sheet_type":"WORKSHEET"}
                for i,s in enumerate(r.json().get("value") or [])]

    @staticmethod
    def _sheet_name(v: str) -> str:
        v=str(v or "").strip()
        if not v: raise ValueError("sheet_name is required.")
        return v

    def _range(self, *, drive_id:str,item_id:str,sheet_name:str,address:str,
               session_id:Optional[str]=None) -> Dict[str,Any]:
        sheet=quote(self._sheet_name(sheet_name),safe="")
        addr=str(address).replace("'","''")
        url=f"{self._base(drive_id,item_id)}/workbook/worksheets/{sheet}/range(address='{addr}')"
        r=requests.get(url,headers=self._headers(session_id),timeout=self.timeout_seconds)
        self._raise_for_graph_error(r,"read Excel worksheet range")
        return r.json()

    def _used_range(self, *, drive_id:str,item_id:str,sheet_name:str,
                    values_only:bool=True) -> Dict[str,Any]:
        sheet=quote(self._sheet_name(sheet_name),safe="")
        url=(f"{self._base(drive_id,item_id)}/workbook/worksheets/{sheet}/usedRange"
             f"(valuesOnly={'true' if values_only else 'false'})")
        r=requests.get(url,headers=self._headers(),timeout=self.timeout_seconds)
        self._raise_for_graph_error(r,"read Excel worksheet used range")
        return r.json()

    def read_rows(self, *, drive_id:str,item_id:str,sheet_name:str,
                  header_row:int=1,limit:Optional[int]=None) -> List[Dict[str,Any]]:
        header_row=max(1,int(header_row))
        safe_limit=max(1,min(int(limit),100000)) if limit is not None else None
        p=self._used_range(drive_id=drive_id,item_id=item_id,sheet_name=sheet_name)
        values=p.get("values") or []
        if not values: return []
        start=self._range_start_row(p.get("address")) or 1
        idx=header_row-start
        if idx < 0 or idx >= len(values):
            raise ValueError(f"Header row {header_row} is outside the Excel used range.")
        headers=self._normalize_headers(values[idx])
        out=[]
        for raw in values[idx+1:]:
            row={h:self._normalize_cell_value(raw[i] if i<len(raw) else None)
                 for i,h in enumerate(headers)}
            if not self._row_is_empty(row):
                out.append(row)
            if safe_limit is not None and len(out)>=safe_limit: break
        return out

    def _header_map(self, *, drive_id:str,item_id:str,sheet_name:str,
                    header_row:int) -> Dict[str,int]:
        p=self._range(drive_id=drive_id,item_id=item_id,sheet_name=sheet_name,
                      address=f"A{header_row}:XFD{header_row}")
        values=p.get("values") or []
        if not values: raise ValueError("Excel worksheet header row is empty.")
        result={}
        for i,v in enumerate(values[0]):
            h=self._normalize_header(v,fallback="")
            if not h: continue
            if h in result:
                raise ValueError(f"Duplicate normalized Excel header: {h}")
            result[h]=i
        if not result: raise ValueError("Excel worksheet does not contain usable headers.")
        return result

    def read_column_cells(self, *, drive_id:str,item_id:str,sheet_name:str,
                          column_name:str,header_row:int=1,
                          limit:Optional[int]=None) -> List[Dict[str,Any]]:
        header_row=max(1,int(header_row))
        limit=max(1,min(int(limit),100000)) if limit is not None else 100000
        col=self._normalize_header(column_name,fallback="")
        hm=self._header_map(drive_id=drive_id,item_id=item_id,sheet_name=sheet_name,
                            header_row=header_row)
        if col not in hm: raise ValueError(f"Excel remediation target column not found: {col}")
        letter=self._column_index_to_letter(hm[col]+1)
        first=header_row+1; last=first+limit-1
        p=self._range(drive_id=drive_id,item_id=item_id,sheet_name=sheet_name,
                      address=f"{letter}{first}:{letter}{last}")
        return [{"row_number":first+i,"column_name":col,
                 "value":row[0] if row else None}
                for i,row in enumerate(p.get("values") or [])]

    def inspect_governed_write_safety(self, *, drive_id:str,item_id:str,
                                      sheet_name:str,updates:List[Dict[str,Any]],
                                      header_row:int=1,max_updates:int=10000)->Dict[str,Any]:
        header_row=max(1,int(header_row))
        if not isinstance(updates,list): raise ValueError("updates must be a list.")
        if not 1 <= max_updates <= 10000: raise ValueError("max_updates must be 1..10000.")
        if len(updates)>max_updates: raise ValueError(f"Too many updates; max {max_updates}.")
        if not updates:
            return {"safe_to_write":True,"reason":"NO_UPDATES","target_count":0,
                    "direct_formula_target_count":0}
        hm=self._header_map(drive_id=drive_id,item_id=item_id,sheet_name=sheet_name,
                            header_row=header_row)
        targets=[]; seen=set()
        for n,u in enumerate(updates,1):
            if not isinstance(u,dict): raise ValueError(f"Update {n} must be an object.")
            try: row=int(u.get("row_number"))
            except (TypeError,ValueError) as exc: raise ValueError(f"Update {n} invalid row_number.") from exc
            if row<=header_row: raise ValueError("Governed Excel remediation cannot modify header rows.")
            col=self._normalize_header(u.get("column_name"),fallback="")
            if col not in hm: raise ValueError(f"Excel remediation target column not found: {col}")
            address=f"{self._column_index_to_letter(hm[col]+1)}{row}"
            if address in seen: raise ValueError(f"Duplicate Excel cell update: {address}")
            seen.add(address); targets.append(address)
        formula_targets=0
        for address in targets:
            p=self._range(drive_id=drive_id,item_id=item_id,sheet_name=sheet_name,address=address)
            f=p.get("formulas") or []
            value=f[0][0] if f and f[0] else None
            if isinstance(value,str) and value.lstrip().startswith("="): formula_targets+=1
        return {"safe_to_write":formula_targets==0,
                "reason":"SAFE_TO_WRITE" if formula_targets==0 else "TARGET_CELL_CONTAINS_FORMULA",
                "target_count":len(targets),"direct_formula_target_count":formula_targets}

    def _create_session(self,drive_id:str,item_id:str)->Optional[str]:
        r=requests.post(f"{self._base(drive_id,item_id)}/workbook/createSession",
                        headers=self._headers(),json={"persistChanges":True},
                        timeout=self.timeout_seconds)
        self._raise_for_graph_error(r,"create Excel workbook session")
        return str(r.json().get("id") or "").strip() or None

    def _close_session(self,drive_id:str,item_id:str,session_id:str)->None:
        r=requests.post(f"{self._base(drive_id,item_id)}/workbook/closeSession",
                        headers=self._headers(session_id),json={},
                        timeout=self.timeout_seconds)
        self._raise_for_graph_error(r,"close Excel workbook session")

    def apply_governed_cell_updates(self, *, drive_id:str,item_id:str,sheet_name:str,
                                    updates:List[Dict[str,Any]],header_row:int=1,
                                    max_updates:int=5000)->Dict[str,Any]:
        safety=self.inspect_governed_write_safety(
            drive_id=drive_id,item_id=item_id,sheet_name=sheet_name,updates=updates,
            header_row=header_row,max_updates=max_updates)
        if not safety["safe_to_write"]:
            raise ValueError("Governed Excel remediation blocked: target cell contains a formula.")
        if not updates:
            return {"success":True,"updated_cells":0,"requested_updates":0,
                    "message":"No Excel cell updates were required."}
        hm=self._header_map(drive_id=drive_id,item_id=item_id,sheet_name=sheet_name,
                            header_row=max(1,int(header_row)))
        session=self._create_session(drive_id,item_id); count=0
        try:
            for u in updates:
                row=int(u["row_number"])
                col=self._normalize_header(u.get("column_name"),fallback="")
                address=f"{self._column_index_to_letter(hm[col]+1)}{row}"
                sheet=quote(self._sheet_name(sheet_name),safe="")
                addr=address.replace("'","''")
                url=f"{self._base(drive_id,item_id)}/workbook/worksheets/{sheet}/range(address='{addr}')"
                r=requests.patch(url,headers=self._headers(session),
                                 json={"values":[[u.get("value")]]},
                                 timeout=self.timeout_seconds)
                self._raise_for_graph_error(r,"apply governed Excel cell update")
                count+=1
        finally:
            if session: self._close_session(drive_id,item_id,session)
        return {"success":True,"drive_id":drive_id,"item_id":item_id,
                "sheet_name":sheet_name,"requested_updates":len(updates),
                "updated_cells":count,
                "message":"Governed Excel remediation applied successfully."}

    @classmethod
    def _normalize_headers(cls,headers:List[Any])->List[str]:
        out=[]; seen={}
        for i,v in enumerate(headers):
            h=cls._normalize_header(v,fallback=f"column_{i+1}")
            seen[h]=seen.get(h,0)+1
            out.append(h if seen[h]==1 else f"{h}_{seen[h]}")
        return out

    @staticmethod
    def _normalize_header(value:Any,*,fallback:str)->str:
        t=str(value if value is not None else "").strip().lower()
        t=re.sub(r"[^a-z0-9]+","_",t); t=re.sub(r"_+","_",t).strip("_")
        if not t:return fallback
        return f"field_{t}" if t[0].isdigit() else t

    @staticmethod
    def _normalize_cell_value(value:Any)->Any:
        if value is None:return None
        if isinstance(value,str):
            v=value.strip(); return v if v else None
        return value

    @staticmethod
    def _row_is_empty(row:Dict[str,Any])->bool:
        return all(v is None or (isinstance(v,str) and not v.strip()) for v in row.values())

    @staticmethod
    def _column_index_to_letter(n:int)->str:
        if n<1:raise ValueError("column_count must be at least 1.")
        s=""
        while n:
            n,r=divmod(n-1,26); s=chr(65+r)+s
        return s

    @staticmethod
    def _range_start_row(address:Any)->Optional[int]:
        m=re.search(r"![A-Z]+(\d+)",str(address or ""),re.I)
        return int(m.group(1)) if m else None
