import os
import gc
import json
from flask import Flask, request, jsonify
import re
import math
import cloudconvert

app = Flask(__name__)

# ==========================================
# 🔑 AYARLAR
# ==========================================
CLOUDCONVERT_API_KEY = "BURAYA_API_KEYINI_YAPISTIR" 

cloudconvert.configure(api_key=CLOUDCONVERT_API_KEY)

# ==========================================
# 🏗️ HESAPLAMA MOTORU (Manuel Okuma Modu)
# ==========================================
class RebarExtractor:
    def __init__(self):
        self.rebar_pattern = re.compile(r'(\d+)\s*[Ø|Q|q|fi]\s*(\d+)(?:\s*L\s*=\s*(\d+))?', re.IGNORECASE)

    def parse_dxf_stream(self, file_path):
        extracted_data = []
        try:
            with open(file_path, 'r', encoding='cp1252', errors='ignore') as fp:
                in_text_entity = False
                while True:
                    code_line = fp.readline()
                    if not code_line: break
                    value_line = fp.readline()
                    if not value_line: break
                    
                    try:
                        code = int(code_line.strip())
                        value = value_line.strip()
                    except:
                        continue

                    if code == 0:
                        in_text_entity = (value == 'TEXT' or value == 'MTEXT')
                    
                    if in_text_entity and code == 1:
                        match = self.rebar_pattern.search(value)
                        if match:
                            try:
                                count = int(match.group(1))
                                diameter = int(match.group(2))
                                length = int(match.group(3)) if match.group(3) else 0
                                extracted_data.append({
                                    "raw_text": value,
                                    "count": count,
                                    "diameter": diameter,
                                    "length_cm": length
                                })
                            except:
                                continue
            return extracted_data
        except Exception as e:
            print(f"Manuel Okuma Hatası: {e}")
            return {"error": f"Dosya okuma hatası: {str(e)}"}

class MaterialCalculator:
    def __init__(self):
        self.unit_weights = {8: 0.395, 10: 0.617, 12: 0.888, 14: 1.208, 16: 1.580, 18: 2.000, 20: 2.470}
        self.stock_bar_length_m = 12.0 

    def calculate_needs(self, parsed_data):
        summary = {} 
        for item in parsed_data:
            diameter = item['diameter']
            count = item['count']
            length_cm = item['length_cm']
            
            if diameter not in self.unit_weights: continue

            length_m = length_cm / 100.0
            total_item_length_m = length_m * count

            if diameter not in summary: summary[diameter] = {"total_length_m": 0.0}
            summary[diameter]["total_length_m"] += total_item_length_m

        final_report = {}
        total_project_tonnage = 0.0

        for dia, data in summary.items():
            total_len = data["total_length_m"]
            unit_w = self.unit_weights[dia]
            weight_kg = total_len * unit_w
            stock_bars = math.ceil(total_len / self.stock_bar_length_m)

            final_report[f"Q{dia}"] = {
                "toplam_agirlik_kg": round(weight_kg, 2),
                "toplam_metraj_m": round(total_len, 2),
                "gerekli_cubuk_adet": stock_bars
            }
            total_project_tonnage += weight_kg

        return {
            "demir_listesi": final_report,
            "toplam_tonaj_kg": round(total_project_tonnage, 2),
            "okunan_veri_sayisi": len(parsed_data)
        }

# ==========================================
# ☁️ CLOUDCONVERT (DÜZELTİLMİŞ)
# ==========================================
def convert_dwg_to_dxf(input_path):
    try:
        print("CloudConvert işlemi başlatılıyor...")
        
        job = cloudconvert.Job.create(payload={
            "tag": "dwg_to_dxf",
            "tasks": {
                "import-my-file": {
                    "operation": "import/upload"
                },
                "convert-my-file": {
                    "operation": "convert",
                    "input": "import-my-file",
                    "output_format": "dxf"
                },
                "export-my-file": {
                    "operation": "export/url",
                    "input": "convert-my-file"
                }
            }
        })

        # --- DÜZELTME BURADA ---
        # Gelen cevabın yapısını kontrol ediyoruz
        print("Job Oluşturuldu, Cevap:", json.dumps(job, indent=2)) # Loglarda görelim

        # Bazen cevap 'data' anahtarının içinde gelir
        job_data = job
        if 'data' in job and 'tasks' not in job:
            job_data = job['data']
        
        if 'tasks' not in job_data:
            print("HATA: CloudConvert cevabında 'tasks' bulunamadı!")
            return None

        # Görevleri al
        upload_task = next(task for task in job_data['tasks'] if task['name'] == 'import-my-file')
        
        with open(input_path, 'rb') as f:
            cloudconvert.Task.upload(file_name=input_path, task=upload_task)

        # Bekle
        job = cloudconvert.Job.wait(id=job_data['id'])
        
        # Bekleme sonrası cevap yapısını tekrar kontrol et
        job_data = job
        if 'data' in job and 'tasks' not in job:
            job_data = job['data']

        if job_data['status'] == 'error':
            print("CloudConvert Hatası:", json.dumps(job_data, indent=2))
            return None

        export_task = next(task for task in job_data['tasks'] if task['name'] == 'export-my-file')
        
        if export_task['status'] != 'finished':
            print("Export bitmedi:", export_task)
            return None

        file_url = export_task['result']['files'][0]['url']
        
        output_filename = input_path + ".dxf"
        cloudconvert.download(filename=output_filename, url=file_url)
        
        print("Dönüştürme ve indirme başarılı:", output_filename)
        return output_filename

    except Exception as e:
        print(f"Convert Hatası Detaylı: {str(e)}")
        return None

# ==========================================
# 🌐 WEB SUNUCUSU
# ==========================================
@app.route('/', methods=['GET'])
def home():
    return "İnşaat API (CloudConvert Fix) Çalışıyor! 🏗️"

@app.route('/analiz-et', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'Dosya bulunamadı'}), 400
    
    file = request.files['file']
    filename = file.filename.lower()
    filepath = os.path.join("/tmp", file.filename)
    file.save(filepath)

    target_dxf_path = filepath
    converted_file_created = False

    try:
        # DWG ise Çevir
        if filename.endswith('.dwg'):
            print(f"DWG tespit edildi: {filename}")
            converted_path = convert_dwg_to_dxf(filepath)
            if converted_path:
                target_dxf_path = converted_path
                converted_file_created = True
            else:
                return jsonify({'error': 'DWG dönüştürme başarısız (Loglara bakınız).'}), 500

        # Veriyi Çıkar
        print(f"Analiz ediliyor: {target_dxf_path}")
        extractor = RebarExtractor()
        raw_data = extractor.parse_dxf_stream(target_dxf_path)

        if isinstance(raw_data, dict) and "error" in raw_data:
            return jsonify(raw_data), 500
        
        if not raw_data:
             return jsonify({
                 'error': 'Dosyada okunabilir demir verisi bulunamadı.',
                 'demir_listesi': {},
                 'toplam_tonaj_kg': 0
             }), 200

        # Hesabı Yap
        calculator = MaterialCalculator()
        result = calculator.calculate_needs(raw_data)
        
        return jsonify(result)

    except Exception as e:
        print(f"Sunucu Hatası: {e}")
        return jsonify({'error': f'Sunucu Hatası: {str(e)}'}), 500

    finally:
        try:
            if os.path.exists(filepath): os.remove(filepath)
            if converted_file_created and os.path.exists(target_dxf_path):
                os.remove(target_dxf_path)
            gc.collect()
        except:
            pass

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
