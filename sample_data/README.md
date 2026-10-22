# Thư mục Dữ liệu Mẫu (Sample / Demo Data) 📁

Thư mục này được sử dụng để chạy ứng dụng **PDF Manipulation Tool** ở chế độ **Offline / Demo** khi:
- Show ứng dụng cho khách hàng, đối tác hoặc người ngoài xem.
- Máy tính không có kết nối internet hoặc không có file khóa `config/credentials.json`.
- Thử nghiệm các tính năng bóc tách, khử trùng lặp và ghép file PDF một cách an toàn mà không ảnh hưởng tới dữ liệu sản xuất thật.

---

## 🗂 Cấu trúc các thư mục:

```text
sample_data/
├── pfl/             # Dữ liệu cho phân hệ PDF Merger (PFL)
├── trim/            # Dữ liệu cho phân hệ PDF Merger (Trim)
├── rfid_ht/         # Dữ liệu cho phân hệ PDF Merger (RFID HT)
├── pfl_thermal/     # Dữ liệu cho phân hệ PDF Merger (PFL Thermal)
└── rfid_sb_uq/      # Dữ liệu cho phân hệ PDF Merger (RFID SB UQ)
```

---

## 🛠 Hướng dẫn đưa dữ liệu vào để Demo:

Mỗi thư mục module có cấu trúc gồm:
- **`metadata.json`**: File định nghĩa danh sách mã đơn hàng SOL, số Job, hoặc số Ticket Express (TE). Bạn có thể mở file này bằng Notepad hoặc VS Code để chỉnh sửa mã đơn cho khớp với file PDF của bạn.
- **`soview/`**: Bạn hãy copy các file PDF SOview mẫu vào thư mục này.
- **`layout/`**: Bạn hãy copy các file PDF bản vẽ Layout / TE mẫu vào thư mục này.
- **`output/`**: Thư mục trống nơi phần mềm sẽ tự động xuất file PDF thành phẩm sau khi bấm `Export PDF`.

---

## 🚀 Cách chạy trên ứng dụng:

1. Mở ứng dụng `PDF Manipulation Tool`.
2. Chọn module bạn muốn demo (ví dụ: `PDF Merger (PFL)` hoặc `PDF Merger (Trim)`).
3. Tại màn hình nhập liệu, bấm nút **`📁 Load Sample Data`**.
4. Ứng dụng sẽ tự động điền đường dẫn các thư mục `soview`, `layout`, `output` và nạp danh sách mã đơn `SOL`.
5. Bấm **`Auto Detect`** và trải nghiệm toàn bộ quy trình tự động hoàn toàn offline!
