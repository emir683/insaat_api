import os
import gc
import json
import re
import math
import unicodedata
from flask import Flask, request, jsonify
import cloudconvert

app = Flask(__name__)

# ===================================================
# 🔑 API KEY (ENV HARİÇ DÜZENLEME YAPILMADI)
# ===================================================
CLOUDCONVERT_API_KEY = """
eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJhdWQiOiIxIiwianRpIjoiMTc1NTNjODEwNDAxYmRlZWU2ODZlMWViZDIyODVhYWY4Y2YwYzZmZTczMTQ0YTFmYzk2NGI4MWQ4MTM1MDY3Yzk4YmFlNmE2N2U4ZDUwOGMiLCJpYXQiOjE3NjU1NTg0NDYuODQzNjIxLCJuYmYiOjE3NjU1NTg0NDYuODQzNjIyLCJleHAiOjQ5MjEyMzIwNDYuODM5NTY0LCJzdWIiOiI3MzcxNzA2MyIsInNjb3BlcyI6WyJ1c2VyLnJlYWQiLCJ1c2VyLndyaXRlIiwidGFzay5yZWFkIiwidGFzay53cml0ZSJdfQ.Ot3krRvghGyEInTmF5SpjD_hszWHuMhYjTnhmQXVoGsZTYyXzwfnJGlbQv0BzTZpG6mGrED_yufHrtLctZQVUUeQVkElyEIMkcFys-uKt8EHnFHof9rMFL5JpGLzOr_3bunUeB8AtSXjeJae0Boj81ZOgZBJ8zV79_13rHhIN0vdxJ5BkffDcorxFGzImjJnSPT5lEEmA0Wce9XtvE1JGvFnZ3EIo9ag86vjTadANq_qsrjMWMYuisaRz6xTeOfcutnYaFQdheFFBSDhb-kDbogZsL4GjjlGszechORtjdqQoqX1IC4sDKS0mFt9Tk48rVKBBPsJTsukpETtLxjqoTBN4xE6k0dghc3sH6XnpGOLuzZTakrCSqQqjY1D29IbyGqowLD9xs6wldX-Lk80yhdZJ486QmwcwZee3hD9zYSIEXg1BOhESMzEau_qEcuEB4g1exYBhgpqvU3nV0EvH1gdcM-keK3qi7RG0mWyDJSNrgozvPH-1CdZ4ruibhcXGCvo2JF50H6q-5MdZ0L0SPMeLyhb679BaGKxPY33ta4zFkLkeObPS_rSZtupKyI4JmDzGBbfq6yqKPP0hVfT03Mv5ir_W7io_loD3DRV4rSalzIou1dtJttJICbI4PXyQttKNQmdxAMjA2fruO1Vl0-W4P30LbIQRZv55Ev0Qks

""".strip()
cloudconvert.configure(api_key=CLOUDCONVERT_API_KEY)

# ===================================================
# 🧹 DOSYA ADI TEMİZLEYİCİ
# ===================================================
def secure_filename(filename):
    filename = unicodedata.normalize('NFKD', filename).encode('ascii', 'ignore').decode('ascii')
    filename = re.sub(r'[^\w\s.-]', '', filename).strip().lower()
    return re.sub(r'[-\s]+', '_', filename)

# ===================================================
# 📖 DXF STREAM OKUYUCU (Encoding Sorunu Çözüldü)
# ===================================================
def open_dxf_safely(file_path):
    """DXF dosyasını en uygun encoding ile aç"""
    try:
        return open(file_path, "r", encoding="utf-8")
    except:
        return open(file_path, "r", encoding="cp1252", errors="ignore")

def dxf_tag_generator(file_path):
    fp = open_dxf_safely(file_path)
    with fp:
        while True:
            code_line = fp.readline()
            if not code_line:
                break
            value_line = fp.readline()
            if not value_line:
                break
            try:
                yield int(code_line.strip()), value_line.strip()
            except:
                continue

# ===================================================
# 🔍 MTEXT Temizleyici
# ===================================================
def clean_mtext(text):
    """
    DXF içindeki {\\P, } gibi formatları temizler.
    MTEXT formatlarını okunabilir hale getirir.
    """
    text = text.replace("\\P", " ")
    text = text.replace("\\~", "")
    text = re.sub(r"{\\.*?}", "", text)
    text = re.sub(r"[{}]", "", text)
    return text.strip()

# ===================================================
# 🏗️ Geliştirilmiş Regex
# ===================================================
EXTENDED_REBAR_REGEX = re.compile(
    r'(\d+)\s*(?:adet|ad)?\s*[xX×*]?\s*(?:Ø|Φ|φ|Q|#|fi|FI|N)?\s*[-]?\s*(\d{1,2})\s*(?:mm)?\s*(?:L|Boy)?\s*[:=]?\s*(\d+)?',
    re.IGNORECASE
)

# ===================================================
# 🧠 HESAPLAMA MOTORU
# ===================================================
class RebarExtractor:

    def parse_dxf_stream(self, file_path):
        results = []
        in_entity = False
        text_buffer = ""

        for code, value in dxf_tag_generator(file_path):

            if code == 0:  
                in_entity = value in ("TEXT", "MTEXT", "ATTRIB", "INSERT")
                text_buffer = ""
                continue

            if in_entity and code == 1:
                text = clean_mtext(value)
                text_buffer += " " + text

                match = EXTENDED_REBAR_REGEX.search(text_buffer)
                if match:
                    try:
                        adet = int(match.group(1))
                        cap = int(match.group(2))
                        uzunluk = int(match.group(3)) if match.group(3) else 0

                        if 6 <= cap <= 40:
                            results.append({
                                "raw_text": text_buffer.strip(),
                                "count": adet,
                                "diameter": cap,
                                "length_cm": uzunluk
                            })
                    except:
                        pass

        return results


class MaterialCalculator:

    def __init__(self):
        self.unit_weights = {
            8: 0.395, 10: 0.617, 12: 0.888, 14: 1.208,
            16: 1.580, 18: 2.000, 20: 2.470, 22: 2.980, 24: 3.550
        }
        self.stock_bar_length_m = 12.0

    def calculate_needs(self, data):
        summary = {}
        total_tonnage = 0

        for item in data:
            cap = item["diameter"]
            adet = item["count"]
            len_cm = item["length_cm"]

            if cap not in self.unit_weights:
                continue

            length_m = (len_cm / 100) if len_cm > 0 else 1.0
            total_m = length_m * adet

            if cap not in summary:
                summary[cap] = 0

            summary[cap] += total_m

        report = {}

        for cap, total_m in summary.items():
            weight = total_m * self.unit_weights[cap]
            bars = math.ceil(total_m / self.stock_bar_length_m)

            report[f"Ø{cap}"] = {
                "toplam_agirlik_kg": round(weight, 2),
                "toplam_metraj_m": round(total_m, 2),
                "gerekli_cubuk_adet": bars
            }
            total_tonnage += weight

        return {
            "demir_listesi": report,
            "toplam_tonaj_kg": round(total_tonnage, 2),
            "okunan_veri_sayisi": len(data)
        }

# ===================================================
# ☁️ CloudConvert DWG → DXF (Stabil Sürüm)
# ===================================================
def convert_dwg_to_dxf(input_path, original_name):
    try:
        print("CloudConvert dönüştürme başlıyor:", original_name)

        job = cloudconvert.Job.create(payload={
            "tag": "dwg_to_dxf",
            "tasks": {
                "upload-file": {"operation": "import/upload"},
                "convert-file": {
                    "operation": "convert",
                    "input": "upload-file",
                    "output_format": "dxf"
                },
                "export-file": {
                    "operation": "export/url",
                    "input": "convert-file"
                }
            }
        })

        job_id = job["data"]["id"]
        tasks = job["data"]["tasks"]

        upload_task = next(t for t in tasks if t["name"] == "upload-file")
        cloudconvert.Task.upload(
            file_name=input_path,
            task=upload_task["id"]
        )

        # Bekle
        job = cloudconvert.Job.wait(id=job_id)
        tasks = job["data"]["tasks"]

        export_task = next(t for t in tasks if t["name"] == "export-file")
        file_url = export_task["result"]["files"][0]["url"]

        # ✔ Doğru dosya ismi
        base, _ = os.path.splitext(input_path)
        output_file = base + ".dxf"

        cloudconvert.download(filename=output_file, url=file_url)

        print("Dönüştürme OK:", output_file)
        return output_file

    except Exception as e:
        print("CloudConvert Hatası:", e)
        return None

# ===================================================
# 🌐 FLASK API
# ===================================================
@app.route("/analiz-et", methods=["POST"])
def analiz_et():

    if "file" not in request.files:
        return jsonify({"error": "Dosya yok"}), 400

    file = request.files["file"]
    clean_name = secure_filename(file.filename)

    path = os.path.join("/tmp", clean_name)
    file.save(path)

    target_path = path
    converted = False

    try:
        # DWG ise çevir
        if clean_name.endswith(".dwg"):
            newfile = convert_dwg_to_dxf(path, clean_name)
            if newfile:
                target_path = newfile
                converted = True
            else:
                return jsonify({"error": "DWG dönüştürülemedi"}), 500

        # DXF Analiz
        extractor = RebarExtractor()
        data = extractor.parse_dxf_stream(target_path)

        if not data:
            return jsonify({
                "error": "Demir verisi bulunamadı",
                "demir_listesi": {},
                "toplam_tonaj_kg": 0
            })

        calc = MaterialCalculator()
        result = calc.calculate_needs(data)
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        try:
            if os.path.exists(path): os.remove(path)
            if converted and os.path.exists(target_path): os.remove(target_path)
        except:
            pass
        gc.collect()


@app.route("/")
def home():
    return "DXF/DWG Demir Analiz API v2 Çalışıyor! 🏗️"

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
