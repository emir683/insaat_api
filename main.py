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
# 🔑 API AYARLARI
# ===================================================
# BURAYA CloudConvert sitesinden aldığın API anahtarını yapıştır
CLOUDCONVERT_API_KEY = """
eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJhdWQiOiIxIiwianRpIjoiMTc1NTNjODEwNDAxYmRlZWU2ODZlMWViZDIyODVhYWY4Y2YwYzZmZTczMTQ0YTFmYzk2NGI4MWQ4MTM1MDY3Yzk4YmFlNmE2N2U4ZDUwOGMiLCJpYXQiOjE3NjU1NTg0NDYuODQzNjIxLCJuYmYiOjE3NjU1NTg0NDYuODQzNjIyLCJleHAiOjQ5MjEyMzIwNDYuODM5NTY0LCJzdWIiOiI3MzcxNzA2MyIsInNjb3BlcyI6WyJ1c2VyLnJlYWQiLCJ1c2VyLndyaXRlIiwidGFzay5yZWFkIiwidGFzay53cml0ZSJdfQ.Ot3krRvghGyEInTmF5SpjD_hszWHuMhYjTnhmQXVoGsZTYyXzwfnJGlbQv0BzTZpG6mGrED_yufHrtLctZQVUUeQVkElyEIMkcFys-uKt8EHnFHof9rMFL5JpGLzOr_3bunUeB8AtSXjeJae0Boj81ZOgZBJ8zV79_13rHhIN0vdxJ5BkffDcorxFGzImjJnSPT5lEEmA0Wce9XtvE1JGvFnZ3EIo9ag86vjTadANq_qsrjMWMYuisaRz6xTeOfcutnYaFQdheFFBSDhb-kDbogZsL4GjjlGszechORtjdqQoqX1IC4sDKS0mFt9Tk48rVKBBPsJTsukpETtLxjqoTBN4xE6k0dghc3sH6XnpGOLuzZTakrCSqQqjY1D29IbyGqowLD9xs6wldX-Lk80yhdZJ486QmwcwZee3hD9zYSIEXg1BOhESMzEau_qEcuEB4g1exYBhgpqvU3nV0EvH1gdcM-keK3qi7RG0mWyDJSNrgozvPH-1CdZ4ruibhcXGCvo2JF50H6q-5MdZ0L0SPMeLyhb679BaGKxPY33ta4zFkLkeObPS_rSZtupKyI4JmDzGBbfq6yqKPP0hVfT03Mv5ir_W7io_loD3DRV4rSalzIou1dtJttJICbI4PXyQttKNQmdxAMjA2fruO1Vl0-W4P30LbIQRZv55Ev0Qks

""".strip()
cloudconvert.configure(api_key=CLOUDCONVERT_API_KEY)

# ===================================================
# 🧹 DOSYA ADI TEMİZLEYİCİ
# ===================================================
def secure_filename(filename):
    """Dosya adındaki Türkçe karakterleri ve boşlukları temizler."""
    filename = unicodedata.normalize('NFKD', filename).encode('ascii', 'ignore').decode('ascii')
    filename = re.sub(r'[^\w\s.-]', '', filename).strip().lower()
    return re.sub(r'[-\s]+', '_', filename)

# ===================================================
# 🏗️ HESAPLAMA MOTORU (Saf Python - RAM Dostu)
# ===================================================
class RebarExtractor:
    def __init__(self):
        # 1. Genel Donatı Regex: "20 Ø12 L=340" veya "14 adet Q16"
        self.rebar_pattern = re.compile(
            r'(\d+)?\s*(?:adet|ad)?\s*[xX*-]?\s*[Ø|Q|q|fi|FI|Fi|N|n]?\s*[-]?\s*(\d{1,2})\s*(?:mm)?\s*(?:L|l|Boy|boy)?[=:]?\s*(\d+)?', 
            re.IGNORECASE
        )
        # 2. Yapısal Eleman Etiketi: S101, K102, P1 vb.
        self.label_pattern = re.compile(r'\b([SKP][ZB]?\d{2,3})\b', re.IGNORECASE)
        # 3. Etriye Regex: Ø8/15 veya fi8/10
        self.stirrup_pattern = re.compile(r'[Ø|Q|q|fi|FI]\s*(\d{1,2})\s*[/]\s*(\d{1,3})', re.IGNORECASE)

    def parse_dxf_stream(self, file_path):
        extracted_data = []
        structural_elements = []
        current_element = "Genel"

        try:
            with open(file_path, 'r', encoding='cp1252', errors='ignore') as fp:
                while True:
                    line = fp.readline()
                    if not line: break
                    text_val = line.strip()
                    if len(text_val) < 2: continue

                    # Önce etiket kontrolü (S101, K102)
                    label_match = self.label_pattern.search(text_val)
                    if label_match:
                        current_element = label_match.group(1).upper()
                        structural_elements.append(current_element)

                    # Etriye kontrolü (Ø8/15)
                    stirrup_match = self.stirrup_pattern.search(text_val)
                    if stirrup_match:
                        try:
                            dia = int(stirrup_match.group(1))
                            spacing = int(stirrup_match.group(2))
                            extracted_data.append({
                                "type": "stirrup", "diameter": dia, "spacing": spacing,
                                "element": current_element, "raw": text_val
                            })
                            continue
                        except: pass

                    # Normal Donatı kontrolü
                    match = self.rebar_pattern.search(text_val)
                    if match:
                        try:
                            count = int(match.group(1)) if match.group(1) else 1
                            diameter = int(match.group(2))
                            if 8 <= diameter <= 40:
                                length = int(match.group(3)) if match.group(3) else 0
                                extracted_data.append({
                                    "type": "rebar", "count": count, "diameter": diameter,
                                    "length_cm": length, "element": current_element, "raw": text_val
                                })
                        except: pass
            
            return extracted_data, list(set(structural_elements))
        except Exception as e:
            print(f"Okuma Hatası: {e}")
            return [], []

class MaterialCalculator:
    def __init__(self):
        self.unit_weights = {8: 0.395, 10: 0.617, 12: 0.888, 14: 1.208, 16: 1.580, 18: 2.000, 20: 2.470, 22: 2.980, 25: 3.850}
        self.stock_bar_length_m = 12.0 

    def calculate_needs(self, parsed_data, elements):
        summary = {} 
        element_details = {}

        for item in parsed_data:
            dia = item['diameter']
            if dia not in self.unit_weights: continue
            
            if item['type'] == 'rebar':
                count = item['count']
                len_m = (item['length_cm'] / 100.0) if item['length_cm'] > 0 else 1.5
                total_len = len_m * count
            else: # stirrup
                spacing = item['spacing']
                count = int(300 / spacing) if spacing > 0 else 20
                total_len = count * 1.5 # Ortalama çevre
            
            if dia not in summary: summary[dia] = 0.0
            summary[dia] += total_len

            el_name = item['element']
            if el_name not in element_details: element_details[el_name] = []
            element_details[el_name].append({
                "tip": "Etriye" if item['type'] == 'stirrup' else "Donatı",
                "cap": dia, "adet": count, 
                "boy": item.get('length_cm', 0) if item['type'] == 'rebar' else f"Ara:{item.get('spacing')}"
            })

        report = {}
        total_tonnage = 0.0
        for dia, total_m in summary.items():
            weight = total_m * self.unit_weights[dia]
            report[f"Q{dia}"] = {
                "toplam_kg": round(weight, 2),
                "toplam_m": round(total_m, 2),
                "gerekli_cubuk_adet": math.ceil(total_m / self.stock_bar_length_m)
            }
            total_tonnage += weight

        return {
            "demir_listesi": report, "detaylar": element_details,
            "elemanlar": elements, "toplam_tonaj_kg": round(total_tonnage, 2)
        }

# ===================================================
# ☁️ CLOUDCONVERT DWG → DXF (Garantili Akış)
# ===================================================
def convert_dwg_to_dxf(input_path, original_name):
    try:
        print(f"CloudConvert işlemi başlıyor: {original_name}")
        job = cloudconvert.Job.create(payload={
            "tag": "dwg_to_dxf",
            "tasks": {
                "upload-file": {"operation": "import/upload"},
                "convert-file": {"operation": "convert", "input": "upload-file", "output_format": "dxf"},
                "export-file": {"operation": "export/url", "input": "convert-file"}
            }
        })

        # Job cevabı her zaman 'data' anahtarında olmayabilir, kontrol edelim
        job_data = job['data'] if 'data' in job else job
        upload_task = next(t for t in job_data['tasks'] if t['name'] == 'upload-file')
        
        cloudconvert.Task.upload(file_name=input_path, task=upload_task['id'] if 'id' in upload_task else upload_task)

        job = cloudconvert.Job.wait(id=job_data['id'])
        job_data = job['data'] if 'data' in job else job

        if job_data['status'] == 'error': return None

        export_task = next(t for t in job_data['tasks'] if t['name'] == 'export-file')
        file_url = export_task['result']['files'][0]['url']
        
        output_filename = input_path + ".dxf"
        cloudconvert.download(filename=output_filename, url=file_url)
        return output_filename
    except Exception as e:
        print(f"CloudConvert Hatası: {e}")
        return None

# ===================================================
# 🌐 FLASK API
# ===================================================
@app.route('/analiz-et', methods=['POST'])
def upload_file():
    if 'file' not in request.files: return jsonify({'error': 'Dosya yok'}), 400
    file = request.files['file']
    clean_name = secure_filename(file.filename)
    filepath = os.path.join("/tmp", clean_name)
    file.save(filepath)

    target_path = filepath
    converted = False

    try:
        if clean_name.endswith('.dwg'):
            new_file = convert_dwg_to_dxf(filepath, clean_name)
            if new_file:
                target_path = new_file
                converted = True
            else:
                return jsonify({'error': 'DWG dönüştürülemedi'}), 500

        extractor = RebarExtractor()
        data, elements = extractor.parse_dxf_stream(target_path)
        
        if not data:
            return jsonify({'error': 'Dosyada okunabilir demir verisi bulunamadı.'}), 200

        calc = MaterialCalculator()
        result = calc.calculate_needs(data, elements)
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': f'Sunucu Hatası: {str(e)}'}), 500
    finally:
        try:
            if os.path.exists(filepath): os.remove(filepath)
            if converted and os.path.exists(target_path): os.remove(target_path)
            gc.collect()
        except: pass

@app.route('/')
def home():
    return "İnşaat Demir Analiz API v5 (Çalışır Sürüm) Çalışıyor! 🏗️"

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
