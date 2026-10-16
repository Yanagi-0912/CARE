# /// script
# requires-python = ">=3.12"
# dependencies = ["beautifulsoup4==4.14.2", "pymongo==4.16.0", "python-dotenv==1.1.1"]
# ///
"""Official branch directory collector and additive Mongo import.

uv run scripts/sync_pharmacy_branches.py collect --cache-dir /tmp/care-branches
uv run scripts/sync_pharmacy_branches.py import             # preview only
uv run scripts/sync_pharmacy_branches.py import --apply     # backup before writes
uv run scripts/sync_pharmacy_branches.py import --insert-only --apply

Only public business facts are retained. No deletions, no invented coordinates,
and an unreported NHI/prescription status stays unknown (None).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import subprocess
import unicodedata
from functools import lru_cache
from urllib.parse import parse_qs, unquote_plus, urlencode, urljoin, urlparse, quote

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "resources/pharmacy_branches.json"
COLLECTION = "medical_facilities_pharmacy"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def clean(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip().replace("\u200b", "").replace("\x00", "").replace("巿", "市")


@lru_cache(maxsize=50000)
def phone_key(value):
    digits = re.sub(r"\D", "", clean(value).split("#")[0])
    return "0" + digits[3:] if digits.startswith("886") else digits


@lru_cache(maxsize=50000)
def address_key(value):
    text = re.sub(r"^\d{3,6}\s*", "", clean(value)).replace("台", "臺")
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"[一二三四五六七八九十\d]+鄰", "", text)
    text = re.sub(r"(?<=[區鄉鎮市])[^路街段巷弄號]{1,8}里", "", text)
    digits=dict(zip("一二三四五六七八九十",map(str,range(1,11))))
    text=re.sub(r"([一二三四五六七八九十])段",lambda m:digits[m[1]]+"段",text)
    text = re.sub(r"[^\u4e00-\u9fff\d之-]", "", text)
    # Floors can differ between the official postal address and the NHI registry.
    return text.split("號")[0] + ("號" if "號" in text else "")


@lru_cache(maxsize=50000)
def address_sites(value):
    """Compare actual house numbers, including a shop occupying several numbers.

    Shared phone numbers are common for an auxiliary shop and its main branch;
    textual similarity alone must never merge different street numbers.
    """
    text = re.sub(r"^\d{3,6}\s*", "", clean(value)).replace("台", "臺")
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"[一二三四五六七八九十\d]+鄰", "", text)
    text = re.sub(r"(?<=[區鄉鎮市])[^路街段巷弄號]{1,8}[里村]", "", text)
    digits = dict(zip("一二三四五六七八九十", map(str, range(1, 11))))
    text = re.sub(r"([一二三四五六七八九十])段", lambda m: digits[m[1]] + "段", text)
    text = re.sub(r"之([一二三四五六七八九十])", lambda m: "之" + digits[m[1]], text)
    text = re.sub(r"(\d+)號之(\d+)", r"\1之\2號", text)
    text = text.replace("-", "之")
    # A road's name may itself include Chinese numbers, as in 大墩十一街.
    match = re.match(r"^(.*(?:路|街)(?:\d+段)?(?:[\d之]+巷)?(?:[\d之]+弄)?)([^號]+)號", text)
    if not match:
        match = re.match(r"^(.*?)(\d[^號]*)號", text)
    if not match:
        return frozenset()
    prefix, numbers = match.groups()
    return frozenset(prefix + number + "號" for number in re.split(r"[、,.;;&/\s]+", numbers) if re.fullmatch(r"\d+(?:之\d+)?", number))


def coordinates(lat, lng):
    try:
        lat, lng = float(lat), float(lng)
        if math.isfinite(lat) and math.isfinite(lng) and 20 <= lat <= 27 and 118 <= lng <= 123:
            return lat, lng
    except (TypeError, ValueError):
        pass
    return None


def map_coordinates(url):
    text = unquote_plus(url or "")
    for pattern, reverse in [(r"[?&](?:q|query)=\s*([\d.]+),\s*([\d.]+)", False),
                             (r"!3d([\d.]+)!4d([\d.]+)", False),
                             (r"!2d([\d.]+)!3d([\d.]+)", True),
                             (r"@([\d.]+),([\d.]+)", False)]:
        match = re.search(pattern, text)
        if match:
            a, b = match.groups()
            result = coordinates(b, a) if reverse else coordinates(a, b)
            if result:
                return result
    return None


def simple_schedule(hours):
    """Only parse an unqualified daily range; preserve complex text verbatim."""
    text = clean(hours)
    match = re.fullmatch(r"(\d{1,2}):?(\d{2})\s*[-~～]\s*(\d{1,2}):?(\d{2})", text)
    if not match:
        return None
    h1, m1, h2, m2 = map(int, match.groups())
    if h1 > 23 or h2 > 24 or m1 > 59 or m2 > 59 or (h2 == 24 and m2):
        return None
    slot = {"open":f"{h1:02}:{m1:02}", "close":f"{h2:02}:{m2:02}"}
    return {day:{"isClosed":False,"slots":[dict(slot)]} for day in WEEKDAYS}


class DirectoryCollector:
    def __init__(self, cache_dir):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.sources = {}

    def fetch(self, url, name=None):
        key = name or hashlib.sha256(url.encode()).hexdigest() + ".html"
        path = self.cache / key
        if not path.exists():
            temporary = path.with_suffix(path.suffix + ".part")
            subprocess.run(["curl", "-fLsS", "--compressed", "--connect-timeout", "10", "--max-time", "90", url, "-o", str(temporary)], check=True, capture_output=True)
            temporary.replace(path)
        data = path.read_bytes()
        if not data or b"<TITLE>Access Denied</TITLE>" in data[:500]:
            raise ValueError("official source unavailable")
        self.sources[url] = {"sha256":hashlib.sha256(data).hexdigest(), "bytes":len(data)}
        return data.decode("utf-8-sig", errors="replace")

    def soup(self, url, name=None):
        from bs4 import BeautifulSoup
        return BeautifulSoup(self.fetch(url, name), "html.parser")

    def branch(self, brand, source_id, name, address, phone, source_url, *, hours="", lat=None, lng=None, kind="藥局", nhi=None, prescription=None):
        name, address = re.sub(r"\s+", "", clean(name)), re.sub(r"^\d{3,6}\s*", "", clean(address))
        if not name or not address:
            raise ValueError("branch missing name or address")
        if brand.lower() not in name.lower():
            name = brand + name
        result = {"brand":brand, "sourceId":str(source_id), "name":name, "address":address,
                  "phone":clean(phone) or None, "type":kind, "sourceUrl":source_url,
                  "hoursText":clean(hours), "nhiContracted":nhi, "dispensesPrescriptions":prescription}
        point = coordinates(lat, lng)
        if point:
            result.update(latitude=point[0], longitude=point[1], coordinateSource=source_url)
        # A temporary closure is not a missing branch and must not look open.
        result["temporarilyClosed"] = any(term in name for term in ("暫停營業", "整修中", "籌備中"))
        return result

    def cosmed(self):
        url = "https://www.cosmed.com.tw/api/getStore.aspx?t=store&c=&d=&s="
        data = json.loads(self.fetch(url,"cosmed.json"))["data"]
        rows = []
        for item in data:
            rx = item.get("Prescription") == "Y"
            row = self.branch("康是美",item["StoreID"],item["StoreNM"],item["ZipCodeName1"]+item["ZipCodeName2"]+item["Address"],item["Tel"],"https://www.cosmed.com.tw/shop.aspx",lat=item.get("lat"),lng=item.get("lng"),kind="藥局" if rx or "藥局" in item["StoreNM"] else "藥妝門市",prescription=True if rx else None)
            schedule = {}
            for day, prefix in zip(WEEKDAYS, ["Mon"]*5+["Sat","Sun"]):
                start, end = item.get("BizHrs"+prefix+"S",""), item.get("BizHrs"+prefix+"E","")
                parsed = simple_schedule(start+"-"+end)
                if parsed:
                    schedule[day] = parsed[day]
            if len(schedule) == 7:
                row["clinicTime"] = schedule
            rows.append(row)
        return rows

    def greattree(self):
        url = "https://www.greattree.com.tw/stores"
        soup = self.soup(url,"greattree.html")
        rows = []
        for item in soup.select("li.address"):
            name = item.select_one(".fwb").get_text(strip=True)
            address = item.select_one(".link-map + span").get_text(strip=True)
            phone = item.select_one('a[href^="tel:"]')
            link = item.select_one(".btn-more")["href"]
            hours = item.select(".row")[1].select_one(".rd").get_text(" ",strip=True)
            row = self.branch("大樹",link.rsplit("/",1)[-1],name,address,phone.get_text(strip=True) if phone else None,link,hours=hours)
            # The directory announces future openings before their first business day.
            opening = re.match(r"(\d{1,2})/(\d{1,2}).*起",hours)
            if opening:
                today = datetime.now(timezone.utc).date()
                date = today.replace(month=int(opening[1]),day=int(opening[2]))
                if date > today:
                    row["openingDate"] = date.isoformat()
            rows.append(row)
        return rows

    def yourchance(self):
        base="https://www.yourchance.com.tw/index.php?lang=cht&temp=store"
        first = self.soup(base,"yourchance.html")
        total = int(re.search(r"共有\s*(\d+)\s*家",first.get_text())[1])
        rows=[]
        per_page=sum(bool(item.select_one(".th")) and len(item.select(".td"))==3 for item in first.select(".tr"))
        if not per_page:
            raise ValueError("佑全 layout changed")
        for page in range(1, math.ceil(total/per_page)+1):
            url = base if page==1 else base+"&pages="+str(page)
            soup = first if page==1 else self.soup(url)
            for item in soup.select(".tr"):
                heading=item.select_one(".th")
                cells=item.select(".td")
                if not heading or len(cells)<2:
                    continue
                name=heading.get_text(" ",strip=True)
                address=cells[0].get_text(" ",strip=True)
                rows.append(self.branch("佑全",hashlib.sha256(address.encode()).hexdigest()[:16],name,address,cells[1].get_text(),url,hours=cells[2].get_text() if len(cells)>2 else "",nhi=True if heading.find("img",alt="健保特約") else None))
        if len(rows)!=total:
            raise ValueError(f"佑全 expected {total}, got {len(rows)}")
        return rows

    def norbelbaby(self):
        url="https://www.norbelbaby.com.tw/nec/about?page=stronghold"
        soup=self.soup(url,"norbelbaby-locations.html")
        rows=[]
        for item in soup.select("li.location"):
            cells=item.select(":scope > .td")
            facts=cells[1].select("li")
            link=item.select_one("a[aid]")
            rows.append(self.branch("丁丁",link["aid"],cells[0].get_text(),facts[0].get_text(),facts[1].get_text().split("：",1)[-1],url,hours=facts[2].get_text().split("：",1)[-1],nhi=True if item.find("img",alt="健保藥局") else None,kind="醫材門市" if "生活館" in cells[0].get_text() else "藥局"))
        return rows

    def yeschain(self):
        url="https://www.yeschain.com.tw/stores.php"
        soup=self.soup(url,"yeschain.html")
        rows=[]
        for item in soup.select(".stores-list-single-item"):
            name=item.select_one("a.fz-20")
            phone=item.select_one('a[href^="tel:"]')
            labels=item.select(".col.color-grey-500")
            if len(labels)<3:
                raise ValueError("躍獅 layout changed")
            address,hours=labels[1].get_text(),labels[2].get_text()
            link=urljoin(url,name["href"])
            rows.append(self.branch("躍獅",parse_qs(urlparse(link).query)["i"][0],name.get_text(),address,phone.get_text().split("：")[-1] if phone else None,link,hours=hours))
        return rows

    def woodpecker(self):
        base="https://www.woodpecker.com.tw/branch/all/1"
        first=self.soup(base,"woodpecker-stores.html")
        last=max(map(int,re.findall(r"/branch/all/(\d+)",str(first))))
        rows=[]
        for page in range(1,last+1):
            url=base.rsplit("/",1)[0]+"/"+str(page)
            soup=first if page==1 else self.soup(url)
            for table in soup.select(".branch-table"):
                item=table.find_parent("div",class_="box")
                name=item.select_one("a.name").get_text()
                address=table.select_one(".add .txt").get_text()
                tel=table.select_one(".tel .txt")
                hours=table.select_one(".time .txt")
                rows.append(self.branch("啄木鳥",hashlib.sha256(address.encode()).hexdigest()[:16],name,address,tel.get_text() if tel else None,url,hours=hours.get_text() if hours else ""))
        return rows

    def wellcare(self):
        data=json.loads(self.fetch("https://www.wellcare.com.tw/wellcare/tw/store/api/shop","wellcare.json"))
        return [self.branch("維康",i["id"],i["name"],i["address"],i.get("tel"),f'https://www.wellcare.com.tw/wellcare/tw/store/{i["id"]}',lat=i.get("lat"),lng=i.get("lng"),kind="藥局" if "藥局" in i.get("function",[]) else "醫材門市") for i in data]

    def nineyi(self, brand, shop_id, cache_name):
        base="https://webapi.91app.com/webapi/LocationV2/"
        query=f"lat=25.037929&lon=121.548818&startIndex=0&maxCount=1000&r=false&isEnableRetailStore=false&shopId={shop_id}&lang=zh-TW"
        data=json.loads(self.fetch(base+"GetLocationList?"+query,cache_name+".json"))["Data"]
        by_id={i["Id"]:i for i in data["List"]}
        if len(by_id)<data["LocationCount"]:
            cities=json.loads(self.fetch(base+f"GetCityAreaList/{shop_id}?v=0&shopId={shop_id}&IsEnableRetailStore=false",cache_name+"-cities.json"))["Data"]["List"]
            def get_city(city):
                url=base+f'GetLocationListByCity?cityId={city["Id"]}&startIndex=0&maxCount=1000&r=false&shopId={shop_id}&lang=zh-TW'
                result=json.loads(self.fetch(url))["Data"]
                if len(result["List"])!=result["LocationCount"]:
                    raise ValueError("91app city pagination incomplete")
                return result["List"]
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                for items in pool.map(get_city,cities):
                    by_id.update({i["Id"]:i for i in items})
        if len(by_id)!=data["LocationCount"]:
            raise ValueError(f'{brand} expected {data["LocationCount"]}, got {len(by_id)}')
        rows=[]
        for i in by_id.values():
            if i.get("IsDelete") or i.get("IsInvisibleLocation") or not i.get("IsDomestic"):
                continue
            hours="平日 "+clean(i.get("NormalTime"))+"；"+clean(i.get("OperationTime") or i.get("WeekendTime"))
            rows.append(self.branch(brand,i["Id"],i["Name"],i["Address"],str(i.get("TelPrepend") or "")+"-"+str(i.get("Tel") or ""),f'https://www.{"medfirst" if shop_id==41218 else "jpmed"}.com.tw/v2/Shop/StoreList/{shop_id}',hours=hours,lat=i.get("Latitude"),lng=i.get("Longitude"),kind="藥局" if "藥局" in i["Name"] else ("醫材門市" if brand=="杏一" else "藥妝門市")))
        return rows

    def poya(self):
        base="https://www.poya.com.tw/store/"
        first=self.soup(base,"poya.html")
        last=max(map(int,re.findall(r'data-page="(\d+)"',str(first))))
        def get_page(page):
            url=base if page==1 else base+"?page="+str(page)
            soup=first if page==1 else self.soup(url)
            rows=[]
            for item in soup.select(".storeItem"):
                name=item.select_one("h3").get_text()
                facts=item.select(".infoBox li")
                frame=item.select_one("iframe")
                point=map_coordinates(frame.get("src", "")) if frame else None
                address=facts[0].get_text(" ",strip=True)
                # Some official cards omit city/town; derive them from their filter IDs.
                city=item.get("data-city")
                town=item.get("data-country")
                city_input=soup.select_one(f'input[value="{city}"]')
                town_input=soup.select_one(f'input[value="{town}"]')
                if not re.match(r".{2,3}[縣市]",address):
                    city_name=city_input.parent.parent.get_text(strip=True) if city_input else ""
                    town_name=town_input.parent.parent.get_text(strip=True) if town_input else ""
                    address=city_name+town_name+address
                rows.append(self.branch("寶雅",hashlib.sha256((name+address).encode()).hexdigest()[:16],name,address,facts[1].get_text(),url,hours=facts[2].get_text(),lat=point[0] if point else None,lng=point[1] if point else None,kind="藥妝門市"))
            return rows
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            return [row for rows in pool.map(get_page,range(1,last+1)) for row in rows]

    def tomods(self):
        from bs4 import BeautifulSoup
        url="https://www.tomods.com.tw/pages/store"
        soup=self.soup(url,"tomods-stores.html")
        rows=[]
        decoder=json.JSONDecoder()
        # The official Cyberbiz page renders these public card contents from JSON.
        for script in soup.find_all("script"):
            text=script.get_text()
            for match in re.finditer(r"blocksSettings:\s*",text):
                blocks,_=decoder.raw_decode(text[match.end():])
                title=next((b["settings"].get("dict_rich_text_title","") for b in blocks if b["type"]=="title"),"")
                html=next((b["settings"].get("dict_rich_text_content","") for b in blocks if b["type"]=="text"),"")
                lines=BeautifulSoup(html.replace("\\n","\n"),"html.parser").get_text("\n",strip=True)
                address=re.search(r"地址\s*[:：]\s*([^\n]+)",lines)
                if not address:
                    continue
                tel=re.search(r"電話\s*[:：]\s*([^\n]+)",lines)
                hours=re.search(r"營業時間\s*[:：]\s*([\s\S]+?)(?:LINE|$)",lines)
                link=next((b["settings"].get("dict_graphic_button_link","") for b in blocks if b["type"]=="button"),"")
                point=map_coordinates(link)
                row=self.branch("Tomod's",blocks[0]["blockId"],BeautifulSoup(title,"html.parser").get_text(),address[1],tel[1] if tel else None,url,hours=hours[1] if hours else "",kind="藥妝門市",lat=point[0] if point else None,lng=point[1] if point else None)
                row["mapUrl"]=link
                rows.append(row)
        unique={}
        for row in rows:
            if row["sourceId"] in unique and unique[row["sourceId"]]!=row:
                raise ValueError("Tomod's source identity conflict")
            unique[row["sourceId"]]=row
        return list(unique.values())

    def watsons(self):
        """Current legal branches; this does not assert that a store is operating."""
        url="https://data.gcis.nat.gov.tw/od/data/api/FDB8D2C8-573D-4276-BFA4-8D3925ABE1CB?"+urlencode({"$format":"json","$filter":"Business_Accounting_NO eq 23224657","$skip":0,"$top":1000})
        records=json.loads(self.fetch(url,"watsons-registered.json"))
        if len(records)>=1000:
            raise ValueError("company registry needs another page")
        promotion={}
        path=self.cache/"watsons-official-table.txt"
        if path.exists():
            for line in path.read_text().splitlines():
                parts=[clean(v) for v in line.split("|")]
                if len(parts)==4 and re.match(r".{2,3}[縣市]",parts[2]):
                    promotion[address_key(parts[2])] = parts
        else:
            supplement=ROOT/"resources/pharmacy_source_supplements/watsons_official_promotion_20261016.json"
            if supplement.exists():
                data=json.loads(supplement.read_text())
                for item in data["branches"]:
                    promotion[address_key(item["address"])]=[item["name"],item["city"],item["address"],item["phone"]]
        rows=[]
        for item in records:
            if item.get("Branch_Office_Status")!="01":
                continue
            name=item["Branch_Office_Name"]
            address=item["Branch_Office_Location"]
            official=promotion.get(address_key(address))
            row=self.branch("屈臣氏",item["Branch_Office_Business_Accounting_NO"],official[0]+"門市" if official else name,address,official[3] if official else None,"https://www.watsons.com.tw/happyday-store" if official else url,kind="藥妝門市")
            row["sourceType"]="company_registration"
            row["operationalStatusVerified"]=False
            row["listedOnOfficialPromotion"]=bool(official)
            row["registrationSource"]=url
            row["companyRegistrationIds"]=[row["sourceId"]]
            rows.append(row)
        unique={}
        for row in rows:
            key=address_key(row["address"])
            previous=unique.get(key)
            if previous:
                ids=sorted(set(previous["companyRegistrationIds"]+row["companyRegistrationIds"]))
                if row["listedOnOfficialPromotion"] and not previous["listedOnOfficialPromotion"]:
                    unique[key]=row
                unique[key]["companyRegistrationIds"]=ids
                unique[key]["sourceId"]=ids[0]
            else:
                unique[key]=row
        return list(unique.values())

    def matsumoto(self):
        url="https://www.matsumotokiyoshi-tw.com/shopinfo"
        soup=self.soup(url,"matsumoto-shops.html")
        items=[]
        for item in soup.select(".wixui-repeater__item"):
            text=item.get_text("\n",strip=True)
            address=re.search(r"(?:臺|台|新|桃|高).{1,4}[縣市].+",text)
            link=item.select_one('a[href*="matsumotokiyoshi-tw.com/"]')
            if address and link:
                name=item.select_one('[data-testid="richTextElement"]').get_text(strip=True)
                items.append((name,address[0],link["href"]))
        def get_detail(item):
            name,address,link=item
            detail=self.soup(quote(link,safe=":/?=&%"))
            text=clean(detail.get_text(" ",strip=True))
            phone=re.search(r"電話號碼\s+(.+?)\s+營業地址",text)
            hours=re.search(r"營業時間\s+(.+?)(?:< Previous|Next >|Up|活動情報)",text)
            row=self.branch("松本清",urlparse(link).path,name,address,phone[1] if phone else None,link,hours=hours[1] if hours else "",kind="藥妝門市")
            return row
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            return list(pool.map(get_detail,items))


class BranchIndex:
    def __init__(self, documents=()):
        self.by_address={}
        self.by_phone={}
        self.by_source={}
        for doc in documents:
            self.add(doc)

    def add(self, doc):
        self.by_address.setdefault(address_key(doc.get("address")),[]).append(doc)
        self.by_phone.setdefault(phone_key(doc.get("phone")),[]).append(doc)
        for source in doc.get("branchSources",[]):
            self.by_source[(source["brand"],source["sourceId"])]=doc
        for source_id in doc.get("companyRegistrationIds",[]):
            self.by_source[(doc.get("brand"),source_id)]=doc


def match_existing(branch, documents):
    """An address alone is insufficient: hospitals host several distinct shops."""
    phone=phone_key(branch.get("phone"))
    address=address_key(branch["address"])
    index=documents if isinstance(documents,BranchIndex) else BranchIndex(documents)
    candidates=[]
    nearby={str(d["_id"]):d for d in index.by_address.get(address,[])+index.by_phone.get(phone,[])}
    for doc in nearby.values():
        same_address=address_key(doc.get("address"))==address
        same_phone=bool(phone and len(phone)>=8 and phone==phone_key(doc.get("phone")))
        doc_name=clean(doc.get("name"))
        same_name=bool(doc_name) and (doc_name in branch["name"] or branch["name"] in doc_name)
        same_site=bool(address_sites(branch["address"]) & address_sites(doc.get("address")))
        exact_identity=doc.get("name")==branch["name"] and doc.get("address")==branch["address"]
        compatible_phone=not phone or not phone_key(doc.get("phone")) or same_phone
        if exact_identity or (same_phone and (same_address or same_site)) or (same_address and same_name and compatible_phone):
            # The government registry can already contain two legal-name variants
            # of one shop. Prefer the exact unique (name, address) identity.
            score=3 if exact_identity else (2 if same_address and doc_name==clean(branch["name"]) else 1)
            candidates.append((score,doc))
    if not candidates:
        return None
    best=max(score for score,_ in candidates)
    winners=[doc for score,doc in candidates if score==best]
    return winners[0] if len(winners)==1 else None


def check_unique_targets(documents, plan):
    """Fail before any write if the pharmacy name/address index would conflict."""
    identities={(d.get("name"),d.get("address")):d["_id"] for d in documents if d["_collection"]==COLLECTION}
    updates={}
    for entry in plan:
        target=(entry["collection"],entry["id"])
        if target in updates and updates[target]!=entry["update"]:
            raise ValueError("Conflicting source rows for one branch before writes")
        updates[target]=entry["update"]
        if entry["collection"]!=COLLECTION:
            continue
        existing=entry["existing"] or {}
        key=(entry["update"]["name"],entry["update"].get("address",existing.get("address")))
        owner=identities.get(key)
        if owner is not None and owner!=entry["id"]:
            raise ValueError(f"Pharmacy name/address identity conflict before writes: {key!r}")
        identities[key]=entry["id"]


def collect(args):
    collector=DirectoryCollector(args.cache_dir)
    handlers={"康是美":collector.cosmed,"大樹":collector.greattree,"佑全":collector.yourchance,
              "丁丁":collector.norbelbaby,"躍獅":collector.yeschain,"啄木鳥":collector.woodpecker,
              "維康":collector.wellcare,"杏一":lambda:collector.nineyi("杏一",41218,"medfirst"),
              "日藥本舖":lambda:collector.nineyi("日藥本舖",41165,"jpmed"),"寶雅":collector.poya,
              "Tomod's":collector.tomods,"松本清":collector.matsumoto,"屈臣氏":collector.watsons}
    rows=[]
    coverage={}
    for brand,handler in handlers.items():
        try:
            found=handler()
            if not found:
                raise ValueError("empty official directory")
            keys=[(r["brand"],r["sourceId"]) for r in found]
            if len(keys)!=len(set(keys)):
                raise ValueError("duplicate source identities")
            rows.extend(found)
            coverage[brand]={"status":"collected","count":len(found)}
        except Exception as exc:
            coverage[brand]={"status":"incomplete","error":type(exc).__name__+": "+str(exc)[:180]}
        print(json.dumps({brand:coverage[brand]},ensure_ascii=False),flush=True)
    if coverage["屈臣氏"]["status"]=="collected":
        coverage["屈臣氏"]["status"]="registered_branches"
        coverage["屈臣氏"]["reason"]="Active company registrations supplemented by an official promotion list; full operating-store coverage could not be verified because the store API was unavailable."
    coverage["博登"]={"status":"incomplete","reason":"Official HTTPS site unavailable; HTTP site did not provide a verifiable branch directory on 2026-10-16.","sourceUrl":"http://www.prohealthcare.com.tw/"}
    # TFDA is a licensing cross-check, never evidence that every brand store is a pharmacy.
    fda=collector.fetch("https://data.fda.gov.tw/data/opendata/export/35/csv","fda.csv")
    registered=[{"_id":str(n),"name":r["機構名稱"],"address":r["地址縣市別"]+r["地址鄉鎮市區"]+r["地址街道巷弄號"],"phone":r["電話"],"nhi":r["是否為健保特約藥局"]} for n,r in enumerate(csv.DictReader(io.StringIO(fda))) if r["機構狀態"]=="開業"]
    index=BranchIndex(registered)
    for row in rows:
        record=match_existing(row,index)
        if record:
            row["type"]="藥局"
            row["registeredPharmacyName"]=record["name"]
            row["registrationSource"]="https://data.gov.tw/dataset/6134"
            if row["nhiContracted"] is None:
                row["nhiContracted"]={"Y":True,"N":False}.get(record["nhi"])
                row["nhiStatusSource"]=row["registrationSource"]
    output={"schemaVersion":1,"collectedAt":datetime.now(timezone.utc).isoformat(),"coverage":coverage,"sources":collector.sources,"branches":rows}
    Path(args.output).write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n")
    if any("error" in entry for entry in coverage.values()):
        raise SystemExit(1)


def import_branches(args):
    from bson import ObjectId, json_util
    from dotenv import dotenv_values
    from pymongo import MongoClient, UpdateOne
    artifact=json.loads(Path(args.output).read_text())
    config=dotenv_values(ROOT/".env")
    import os
    uri=os.getenv("MONGODB_URI") or config.get("MONGODB_URI")
    database=os.getenv("MONGODB_DB") or config.get("MONGODB_DB")
    if not uri or not database:
        raise ValueError("MONGODB_URI/MONGODB_DB required")
    with MongoClient(uri,serverSelectionTimeoutMS=10000,socketTimeoutMS=30000) as client:
        collections={name:client[database][name] for name in (COLLECTION,"medical_facilities_retail")}
        documents=[{**doc,"_collection":name} for name,col in collections.items() for doc in col.find({})]
        index=BranchIndex(documents)
        plan=[]
        skipped_existing=0
        today=datetime.now(timezone.utc).date().isoformat()
        for row in artifact["branches"]:
            if row.get("openingDate",today)>today:
                continue
            existing=index.by_source.get((row["brand"],row["sourceId"]))
            existing=existing or match_existing(row,index)
            if args.insert_only and existing:
                # Stage additions while an older deployment is still serving;
                # renaming existing records requires the alias-aware reader.
                skipped_existing+=1
                continue
            source={k:row[k] for k in ("brand","sourceId","sourceUrl")}
            source["collectedAt"]=artifact["collectedAt"]
            doc_id=existing["_id"] if existing else ObjectId(hashlib.sha256((row["brand"]+":"+row["sourceId"]).encode()).hexdigest()[:24])
            target=existing["_collection"] if existing else (COLLECTION if row["type"]=="藥局" else "medical_facilities_retail")
            update={"brand":row["brand"],"name":row["name"],"officialHoursText":row["hoursText"],"branchDataUpdatedAt":artifact["collectedAt"]}
            if existing and not existing.get("managedBranch"):
                # Keep government medical type, hours, and notes. Never relabel a
                # licensed pharmacy as an ordinary shop based on an absent icon.
                update["originalName"]=existing.get("originalName",existing["name"])
            else:
                update.update({k:row[k] for k in ("address","phone","type","nhiContracted","dispensesPrescriptions")})
                update["departments"]=[]
                update["managedBranch"]=True
                update["notes"]=("官方營業時間："+row["hoursText"]) if row["hoursText"] else None
                if row.get("sourceType")=="company_registration":
                    update["notes"]="依公司登記資料收錄，實際營業狀態與門市服務請先確認。"
                    update["operationalStatusVerified"]=False
                update["directorySourceType"]=row.get("sourceType","official_directory")
                for field in ("registrationSource","nhiStatusSource","registeredPharmacyName","companyRegistrationIds"):
                    if field in row:
                        update[field]=row[field]
                schedule=row.get("clinicTime") or simple_schedule(row["hoursText"])
                if schedule:
                    update["clinicTime"]=schedule
            if row.get("temporarilyClosed"):
                update["clinicTime"]={day:{"isClosed":True,"slots":[]} for day in WEEKDAYS}
                update["notes"]="官網標示暫停營業。"
            point=coordinates(row.get("latitude"),row.get("longitude"))
            if point and (not existing or not coordinates(existing.get("latitude"),existing.get("longitude"))):
                update.update(latitude=point[0],longitude=point[1],location={"type":"Point","coordinates":[point[1],point[0]]},coordinateSource=row["coordinateSource"])
            aliases=list(dict.fromkeys([row["name"]]+([existing["name"]] if existing else [])))
            plan.append({"id":doc_id,"collection":target,"existing":existing,"update":update,"source":source,"aliases":aliases})
            # Subsequent sources in this same run must deduplicate against inserts.
            if not existing:
                index.add({"_id":doc_id,"_collection":target,**update,"branchSources":[source]})
        check_unique_targets(documents,plan)
        counts={"branches":len(plan),"new":sum(p["existing"] is None for p in plan),"matched":sum(p["existing"] is not None for p in plan),"newWithoutCoordinates":sum(p["existing"] is None and "location" not in p["update"] for p in plan)}
        counts["skippedExisting"]=skipped_existing
        print(json.dumps(counts,ensure_ascii=False),flush=True)
        report_path=Path(args.report_dir)
        report_path.mkdir(parents=True,exist_ok=True)
        (report_path/"import-plan.json").write_text(json.dumps([{**p,"id":str(p["id"])} for p in plan],ensure_ascii=False,default=str,indent=2))
        if not args.apply:
            return
        # Exact preimages are recoverable BSON, including non-projected fields.
        backup={name:list(col.find({"_id":{"$in":[p["id"] for p in plan if p["collection"]==name]}})) for name,col in collections.items()}
        backup_path=report_path/"before-import.bson.json"
        if backup_path.exists():
            raise ValueError("Backup already exists; choose a fresh --report-dir before applying")
        backup_path.write_text(json_util.dumps(backup,ensure_ascii=False))
        inserted_ids={name:[str(p["id"]) for p in plan if p["collection"]==name and p["existing"] is None] for name in collections}
        (report_path/"inserted-ids.json").write_text(json.dumps(inserted_ids,ensure_ascii=False,indent=2)+"\n")
        for name,col in collections.items():
            operations=[UpdateOne({"_id":entry["id"]},{"$set":entry["update"],"$addToSet":{"aliases":{"$each":entry["aliases"]},"branchSources":entry["source"]}},upsert=True) for entry in plan if entry["collection"]==name]
            if operations:
                result=col.bulk_write(operations,ordered=True)
                print(json.dumps({"collection":name,"upserted":result.upserted_count,"modified":result.modified_count}),flush=True)
            col.create_index([("location","2dsphere")])
        counts["afterCount"]={name:col.count_documents({}) for name,col in collections.items()}
        (report_path/"import-result.json").write_text(json.dumps(counts,ensure_ascii=False,indent=2)+"\n")
        print(json.dumps({"applied":counts},ensure_ascii=False),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["collect","import"])
    parser.add_argument("--output",default=str(DEFAULT_OUTPUT))
    parser.add_argument("--cache-dir",default="/tmp/care-pharmacy-sources")
    parser.add_argument("--report-dir",default=str(ROOT.parent/"outputs"/("pharmacy-branches-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))))
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--insert-only",action="store_true",help="Only stage new branches; leave existing records unchanged until the alias-aware search is deployed")
    args=parser.parse_args()
    collect(args) if args.action=="collect" else import_branches(args)
