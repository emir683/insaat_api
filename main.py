import os
from flask import Flask, request, jsonify
import ezdxf
import re
import math

app = Flask(__name__)

# --- HESAPLAMA MOTORU BAŞLANGIÇ ---

class RebarExtractor:
    def __init__(self):
        # Örnek Regex: "20 Ø12 L=340" veya "14 Ø 16"
        self.rebar_pattern = re.compile(r'(\d+)\s*[Ø|Q|q|fi]\s*(\d+)(?:\s*L\s*=\s*(\d+))?', re.IGNORECASE)

    def parse_dxf(self, file_path):
        try:
            doc = ezdxf.readfile(file_path)
            msp = doc.modelspace()
            extracted_data = []
            
            # TEXT ve MTEXT objelerini tara
            for entity in msp.query('TEXT MTEXT'):
                text_content = entity.dxf.text
                
                # Regex ile demir verisi ara
                match = self.rebar_pattern.search(text_content)
                if match:
                    count = int(match.group(1))
                    diameter = int(match.group(2))
                    length = int(match.group(3)) if match.group(3) else 0 
                    
                    extracted_data.append({
                        "raw_text": text_content,
                        "count": count,
                        "diameter": diameter,
                        "length_cm": length
                    })
            
            return extracted_data
        except Exception as e:
            return {"error": str(e)}

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
            
            if diameter not in self.unit_weights:
                continue

            length_m = length_cm / 100.0
            total_item_length_m = length_m * count

            if diameter not in summary:
                summary[diameter] = {"total_length_m": 0.0}

            summary[diameter]["total_length_m"] += total_item_length_m

        final_report = {}
        total_project_tonnage = 0.0

        for dia, data in summary.items():
            total_len = data["total_length_m"]
            unit_w = self.unit_weights[dia]
            weight_kg = total_len * unit_w
            stock_bars = math.ceil(total_len / self.stock_bar_length_m)

            final_report[f"Q{dia}"] = {
                "toplam_metraj_m": round(total_len, 2),
                "toplam_agirlik_kg": round(weight_kg, 2),
                "gerekli_cubuk_adet": stock_bars
            }
            total_project_tonnage += weight_kg

        return {
            "demir_listesi": final_report,
            "toplam_tonaj_kg": round(total_project_tonnage, 2),
            "okunan_veri_sayisi": len(parsed_data)
        }

# --- HESAPLAMA MOTORU BİTİŞ ---

# --- SUNUCU AYARLARI ---

@app.route('/', methods=['GET'])
def home():
    return "İnşaat API Çalışıyor! 🏗️"

@app.route('/analiz-et', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'Dosya bulunamadı'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Dosya seçilmedi'}), 400

    if file:
        # Dosyayı geçici olarak kaydet
        filepath = os.path.join("/tmp", file.filename) if os.name != 'nt' else file.filename
        file.save(filepath)

        # 1. Veriyi Çıkar
        extractor = RebarExtractor()
        raw_data = extractor.parse_dxf(filepath)

        if isinstance(raw_data, dict) and "error" in raw_data:
             # Hata varsa dosyayı sil ve dön
            if os.path.exists(filepath): os.remove(filepath)
            return jsonify(raw_data), 500

        # 2. Hesabı Yap
        calculator = MaterialCalculator()
        result = calculator.calculate_needs(raw_data)

        # İşlem bitince dosyayı sil (Temizlik)
        if os.path.exists(filepath):
            os.remove(filepath)

        return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)