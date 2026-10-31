import sys, ctypes, threading, queue, time, json, math, os, tempfile, uuid, copy
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

# US physical key positions, independent of the Windows text-input language.
SCANS = dict(zip('1234567890', range(2, 12)))
SCANS.update(dict(zip('qwertyuiop', range(16, 26))))
SCANS.update(dict(zip('asdfghjkl', range(30, 39))))
SCANS.update(dict(zip('zxcvbnm', range(44, 51))))
SHIFTED = dict(zip('!@#$%^&*()', '1234567890'))

def key_spec(c):
    base = SHIFTED.get(c, c.lower())
    if base not in SCANS:
        raise ValueError(f'Desteklenmeyen tuş: {c!r}. Harf, rakam ve !@#$%^&*() kullanın.')
    return SCANS[base], c.isupper() or c in SHIFTED

def parse(text):
    events, i = [], 0
    while i < len(text):
        c = text[i]
        if c.isspace(): i += 1; continue
        if c in '|-': events.append(None); i += 1; continue
        if c == '[':
            end = text.find(']', i + 1)
            if end < 0: raise ValueError('Kapanmayan köşeli parantez var.')
            chars = ''.join(text[i+1:end].split())
            if not chars or '[' in chars: raise ValueError('Akor boş veya iç içe olamaz.')
            i = end + 1
        else:
            chars = c; i += 1
        specs = list(dict.fromkeys(key_spec(x) for x in chars))
        if len({scan for scan, _ in specs}) != len(specs):
            raise ValueError(f'[{chars}] aynı fiziksel tuşun iki farklı notasını içeriyor; bunları ayrı yazın.')
        events.append(specs)
    if not events: raise ValueError('Önce nota girin.')
    return events

def play_chord(sender, event, stop, hold):
    normal = [scan for scan, shifted in event if not shifted]
    shifted = [scan for scan, shifted in event if shifted]
    release = [scan for scan, _ in event] + ([42] if shifted else [])
    started = time.perf_counter()
    try:
        if normal:
            sender.send(normal)
        if normal and shifted and stop.wait(0.012):
            return
        if shifted:
            sender.send([42])
            # Allow the game to observe Shift before the altered notes.
            if stop.wait(0.012):
                return
            sender.send(shifted)
        stop.wait(max(0, hold - (time.perf_counter() - started)))
    finally:
        sender.send(release, up=True)

def timed_schedule(score):
    notes = score.get('notes')
    if not isinstance(notes, list) or not notes or len(notes) > 100000:
        raise ValueError('Geçerli bir zamanlı nota listesi yok.')
    result = {}
    for n in notes:
        beat, duration = float(n['beat']), float(n['duration'])
        if not (math.isfinite(beat) and math.isfinite(duration) and 0 <= beat <= 100000 and 0 < duration <= 1000):
            raise ValueError('Geçersiz nota süresi.')
        scan, shift = key_spec(n['key'])
        result.setdefault(beat, {'down': [], 'up': []})['down'].append((scan, shift))
        end = round(beat + duration * .95, 6)
        result.setdefault(end, {'down': [], 'up': []})['up'].append(scan)
    return sorted(result.items())

class SongStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get('APPDATA') or Path.home()) / 'RobloxPiyano' / 'parcalar.json'
        self.songs = []
        self.error = None
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding='utf-8'))
                if payload.get('version') != 1 or not isinstance(payload.get('songs'), list):
                    raise ValueError('Kayıt dosyasının biçimi geçersiz.')
                ids = set()
                for song in payload['songs']:
                    if not isinstance(song.get('id'), str) or song['id'] in ids:
                        raise ValueError('Geçersiz parça kimliği.')
                    ids.add(song['id'])
                    if not isinstance(song.get('name'), str) or not song['name'].strip():
                        raise ValueError('Geçersiz parça adı.')
                    if song.get('kind') == 'text': parse(song['text'])
                    elif song.get('kind') == 'timed': timed_schedule(song['score'])
                    else: raise ValueError('Geçersiz parça türü.')
                    step, hold, delay = map(float, (song['step'], song['hold'], song['delay']))
                    if not (40 <= step <= 10000 and 40 <= hold <= step and 1 <= delay <= 30):
                        raise ValueError('Geçersiz parça ayarları.')
                self.songs = payload['songs']
            except Exception as e:
                self.error = f'Parça kayıtları okunamadı: {e}. Mevcut dosyanın üzerine yazılmayacak.'
    def commit(self, songs):
        if self.error: raise ValueError(self.error)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='parcalar-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump({'version': 1, 'songs': songs}, f, ensure_ascii=False, indent=2)
                f.flush(); os.fsync(f.fileno())
            os.replace(temporary, self.path)
            self.songs = copy.deepcopy(songs)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
    def save(self, entry, song_id=None):
        name = entry['name'].strip()
        if not name or len(name) > 100: raise ValueError('Parça adı 1–100 karakter olmalı.')
        if any(x['name'].casefold() == name.casefold() and x['id'] != song_id for x in self.songs):
            raise ValueError('Bu isimde bir parça zaten var. Listeden yükle veya farklı bir isim ver.')
        songs = copy.deepcopy(self.songs)
        record = copy.deepcopy(entry); record['name'] = name
        record['id'] = song_id or str(uuid.uuid4())
        index = next((i for i,x in enumerate(songs) if x['id'] == song_id), None)
        if song_id and index is None: raise ValueError('Güncellenecek parça bulunamadı.')
        if index is None: songs.append(record)
        else: songs[index] = record
        self.commit(songs)
        return record['id']
    def rename(self, song_id, name):
        entry = copy.deepcopy(next(x for x in self.songs if x['id'] == song_id))
        entry['name'] = name
        return self.save(entry, song_id)
    def delete(self, song_id):
        self.commit([x for x in self.songs if x['id'] != song_id])

class Sender:
    def __init__(self):
        from ctypes import wintypes as w
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [('wVk',w.WORD),('wScan',w.WORD),('dwFlags',w.DWORD),('time',w.DWORD),('dwExtraInfo',ctypes.c_size_t)]
        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [('dx',w.LONG),('dy',w.LONG),('mouseData',w.DWORD),('dwFlags',w.DWORD),('time',w.DWORD),('dwExtraInfo',ctypes.c_size_t)]
        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [('uMsg',w.DWORD),('wParamL',w.WORD),('wParamH',w.WORD)]
        class U(ctypes.Union):
            _fields_ = [('ki',KEYBDINPUT),('mi',MOUSEINPUT),('hi',HARDWAREINPUT)]
        class INPUT(ctypes.Structure):
            _anonymous_ = ('u',)
            _fields_ = [('type',w.DWORD),('u',U)]
        self.I, self.K = INPUT, KEYBDINPUT
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        self.api.SendInput.argtypes = [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self.api.SendInput.restype = w.UINT
        self.api.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.api.GetAsyncKeyState.restype = ctypes.c_short
        self.api.GetForegroundWindow.restype = w.HWND
    def send(self, scans, up=False):
        batch = (self.I * len(scans))()
        for item, scan in zip(batch, scans):
            item.type = 1
            item.ki = self.K(0, scan, 8 | (2 if up else 0), 0, 0)
        if self.api.SendInput(len(batch), batch, ctypes.sizeof(self.I)) != len(batch):
            raise OSError('Tuş gönderilemedi. Oyun ve program aynı yetki seviyesinde çalışmalı.')

class App:
    def __init__(self, root):
        self.root, self.sender = root, Sender()
        self.stop = threading.Event(); self.worker = None; self.messages = queue.Queue(); self.previous = {}; self.timed_score = None; self.score_preview = None
        root.title('Roblox Piyano'); root.geometry('1060x620'); root.minsize(1060, 620)
        self.store = SongStore(); self.current_song_id = None; self.song_ids = []
        outer = ttk.Frame(root); outer.pack(fill='both', expand=True)
        sidebar = ttk.Frame(outer, padding=12, width=245); sidebar.pack(side='left', fill='y')
        ttk.Label(sidebar, text='Kaydedilen parçalar', font=('Segoe UI', 13, 'bold')).pack(anchor='w', pady=(0, 8))
        list_frame = ttk.Frame(sidebar); list_frame.pack(fill='both', expand=True)
        self.song_list = tk.Listbox(list_frame, width=27, font=('Segoe UI', 11), exportselection=False)
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self.song_list.yview)
        self.song_list.configure(yscrollcommand=scrollbar.set)
        self.song_list.pack(side='left', fill='both', expand=True); scrollbar.pack(side='right', fill='y')
        self.song_list.bind('<Double-Button-1>', lambda event: self.load_selected())
        self.song_list.bind('<Return>', lambda event: self.load_selected())
        ttk.Button(sidebar, text='Seçili parçayı yükle', command=self.load_selected).pack(fill='x', pady=(8, 4))
        ttk.Button(sidebar, text='Yeni parça', command=self.new_song).pack(fill='x', pady=4)
        ttk.Button(sidebar, text='İsmini değiştir', command=self.rename_song).pack(fill='x', pady=4)
        ttk.Button(sidebar, text='Listeden sil', command=self.delete_song).pack(fill='x', pady=4)
        frame = ttk.Frame(outer, padding=16); frame.pack(fill='both', expand=True)
        title_row = ttk.Frame(frame); title_row.pack(fill='x', pady=(0,8))
        ttk.Label(title_row, text='Parça adı').pack(side='left', padx=(0,8))
        self.song_name = tk.StringVar()
        ttk.Entry(title_row, textvariable=self.song_name).pack(side='left', fill='x', expand=True)
        ttk.Button(title_row, text='Kaydet / güncelle', command=self.save_song).pack(side='left', padx=(8,0))
        ttk.Label(frame, text='Notaları yaz veya yapıştır', font=('Segoe UI',16,'bold')).pack(anchor='w')
        ttk.Label(frame, text='a s d: sırayla • [ad]: akor • |: bir adım bekle • Büyük harf: Shift (karışık akorlar desteklenir)\nBoşluk ve satır sonları yalnızca ayırıcıdır. F6: başlat / F8: durdur.').pack(anchor='w', pady=8)
        self.text = tk.Text(frame, height=13, font=('Consolas',14), wrap='word'); self.text.pack(fill='both', expand=True)
        self.text.insert('1.0', 'a s d [ad] | d s a')
        row = ttk.Frame(frame); row.pack(fill='x', pady=12)
        self.step = tk.StringVar(value='250'); self.hold = tk.StringVar(value='80'); self.delay = tk.StringVar(value='3')
        for label, var in [('Adım / vuruş (ms)',self.step),('Basılı tut (ms)',self.hold),('Başlama gecikmesi (sn)',self.delay)]:
            ttk.Label(row,text=label).pack(side='left',padx=4); ttk.Entry(row,textvariable=var,width=6).pack(side='left')
        buttons = ttk.Frame(frame); buttons.pack(fill='x')
        ttk.Button(buttons,text='Başlat (F6)',command=self.start).pack(side='left')
        ttk.Button(buttons,text='Durdur (F8)',command=self.stop.set).pack(side='left',padx=8)
        ttk.Button(buttons,text='TXT / zamanlı JSON aç',command=self.load).pack(side='left')
        self.status = tk.StringVar(value='Hazır. Başlattıktan sonra Roblox piyanosuna tıkla; sohbet kapalı olsun.')
        ttk.Label(frame,textvariable=self.status,wraplength=670).pack(anchor='w',pady=10)
        self.refresh_songs()
        if self.store.error:
            root.after(100, lambda: messagebox.showerror('Parça kayıtları', self.store.error))
        root.protocol('WM_DELETE_WINDOW',self.close); root.after(30,self.poll)
    def refresh_songs(self, selected_id=None):
        entries = sorted(self.store.songs, key=lambda x:x['name'].casefold())
        self.song_ids = [x['id'] for x in entries]
        self.song_list.delete(0, 'end')
        for entry in entries: self.song_list.insert('end', entry['name'])
        if selected_id in self.song_ids:
            index = self.song_ids.index(selected_id)
            self.song_list.selection_set(index); self.song_list.see(index)
    def selected_song(self):
        selection = self.song_list.curselection()
        if not selection:
            messagebox.showinfo('Parçalar', 'Önce listeden bir parça seç.'); return None
        return next(x for x in self.store.songs if x['id'] == self.song_ids[selection[0]])
    def stop_for_edit(self):
        self.stop.set()
        if self.worker and self.worker.is_alive(): self.worker.join(timeout=1)
        while not self.messages.empty(): self.messages.get_nowait()
    def show_score(self, score):
        self.timed_score = copy.deepcopy(score)
        groups = {}
        for n in score['notes']: groups.setdefault(n['beat'], []).append(n['key'])
        self.score_preview = ' '.join('['+''.join(keys)+']' if len(keys)>1 else keys[0] for _,keys in sorted(groups.items()))
        self.text.delete('1.0', 'end'); self.text.insert('1.0', self.score_preview)
    def new_song(self):
        self.stop_for_edit()
        self.current_song_id = None; self.timed_score = None; self.score_preview = None
        self.song_name.set(''); self.text.delete('1.0', 'end')
        self.song_list.selection_clear(0, 'end')
        self.status.set('Yeni parça: notaları yapıştır, adını yaz ve Kaydet düğmesine bas.')
    def save_song(self):
        try:
            content = self.text.get('1.0', 'end').strip()
            step, hold, delay = map(float, (self.step.get(), self.hold.get(), self.delay.get()))
            if not (40 <= step <= 10000 and 40 <= hold <= step and 1 <= delay <= 30):
                raise ValueError('Adım: 40–10000 ms; basılı tutma: 40 ms ile adım arası; gecikme: 1–30 sn.')
            entry = {'name':self.song_name.get(), 'step':self.step.get(), 'hold':self.hold.get(), 'delay':self.delay.get()}
            if self.timed_score is not None:
                if content != self.score_preview: raise ValueError('Zamanlı nota önizlemesi değişti. Dosyayı yeniden aç veya Yeni parça ile düz nota kaydet.')
                timed_schedule(self.timed_score)
                entry.update(kind='timed', score=self.timed_score)
            else:
                parse(content); entry.update(kind='text', text=content)
            self.current_song_id = self.store.save(entry, self.current_song_id)
            self.refresh_songs(self.current_song_id)
            self.status.set(f'{entry["name"].strip()} kaydedildi. Notalar ve hız ayarları saklandı.')
        except Exception as e: messagebox.showerror('Kaydet', str(e))
    def load_selected(self):
        song = self.selected_song()
        if song is None: return
        self.stop_for_edit()
        self.current_song_id = song['id']; self.song_name.set(song['name'])
        if song['kind'] == 'timed': self.show_score(song['score'])
        else:
            self.timed_score = None; self.score_preview = None
            self.text.delete('1.0', 'end'); self.text.insert('1.0', song['text'])
        self.step.set(song['step']); self.hold.set(song['hold']); self.delay.set(song['delay'])
        self.status.set(f'{song["name"]} yüklendi. F6 ile başlatabilirsin.')
    def rename_song(self):
        song = self.selected_song()
        if song is None: return
        name = simpledialog.askstring('İsmini değiştir', 'Yeni parça adı:', initialvalue=song['name'], parent=self.root)
        if name is None: return
        try:
            self.store.rename(song['id'], name)
            if self.current_song_id == song['id']: self.song_name.set(name.strip())
            self.refresh_songs(song['id']); self.status.set('Parça adı değiştirildi.')
        except Exception as e: messagebox.showerror('İsmini değiştir', str(e))
    def delete_song(self):
        song = self.selected_song()
        if song is None: return
        if not messagebox.askyesno('Listeden sil', f'{song["name"]} kayıtlı parçalardan silinsin mi?'): return
        try:
            self.store.delete(song['id'])
            if self.current_song_id == song['id']: self.current_song_id = None
            self.refresh_songs(); self.status.set('Parça listeden silindi.')
        except Exception as e: messagebox.showerror('Listeden sil', str(e))
    def load(self):
        path = filedialog.askopenfilename(filetypes=[('Nota dosyaları','*.txt *.json'),('Tüm dosyalar','*.*')])
        if path:
            try:
                self.stop_for_edit()
                with open(path,encoding='utf-8-sig') as f: content=f.read()
                if path.lower().endswith('.json'):
                    score=json.loads(content); timed_schedule(score)
                    bpm=float(score.get('bpm',100))
                    if not math.isfinite(bpm) or not 20 <= bpm <= 500: raise ValueError('Geçersiz BPM.')
                    self.show_score(score)
                    self.step.set(f'{60000/bpm:g}')
                    self.status.set(f"{score.get('title','Zamanlı parça')} yüklendi. Süreler dosyadan okunacak; vuruş süresiyle tempoyu değiştir.")
                else:
                    self.timed_score=None; self.score_preview=None
                    self.text.delete('1.0','end'); self.text.insert('1.0',content)
                self.current_song_id = None
                self.song_list.selection_clear(0, 'end')
                self.song_name.set(score.get('title', Path(path).stem) if path.lower().endswith('.json') else Path(path).stem)
            except Exception as e: messagebox.showerror('Dosya',str(e))
    def start(self):
        if self.worker and self.worker.is_alive(): return
        try:
            content = self.text.get('1.0','end').strip()
            timed = self.timed_score is not None
            if timed and content != self.score_preview:
                raise ValueError('Zamanlı parçanın önizlemesi değişti. JSON dosyasını yeniden aç veya kendi notaların için TXT dosyası yükle.')
            events = timed_schedule(self.timed_score) if timed else parse(content)
            step, hold, delay = float(self.step.get())/1000, float(self.hold.get())/1000, float(self.delay.get())
            if not (0.04 <= step <= 10 and 0.04 <= hold <= step and 1 <= delay <= 30):
                raise ValueError('Adım: 40–10000 ms. Basılı tutma: 40 ms ile adım süresi arası. Gecikme: 1–30 sn.')
        except ValueError as e: messagebox.showerror('Kontrol',str(e)); return
        self.stop.clear(); self.status.set(f'{delay:g} saniye içinde Roblox piyanosuna tıkla. F8 ile durdur.')
        self.worker = threading.Thread(target=self.play_timed if timed else self.play,args=(events,step,hold,delay),daemon=True); self.worker.start()
    def play(self,events,step,hold,delay):
        try:
            if self.stop.wait(delay): return
            target = self.sender.api.GetForegroundWindow()
            for index, event in enumerate(events):
                if self.stop.is_set(): break
                if self.sender.api.GetForegroundWindow() != target:
                    self.messages.put('Pencere değişti; çalma durduruldu.'); return
                started=time.perf_counter()
                if event:
                    play_chord(self.sender, event, self.stop, hold)
                self.messages.put(f'Çalınıyor: {index+1}/{len(events)}')
                if self.stop.wait(max(0,step-(time.perf_counter()-started))): break
            self.messages.put('Durduruldu.' if self.stop.is_set() else 'Tamamlandı.')
        except Exception as e: self.messages.put(f'Hata: {e}')
        finally:
            if self.stop.is_set(): self.messages.put('Durduruldu.')
    def play_timed(self, schedule, beat_seconds, hold, delay):
        held = set()
        try:
            if self.stop.wait(delay): return
            target = self.sender.api.GetForegroundWindow()
            start = time.perf_counter()
            for index, (beat, event) in enumerate(schedule):
                while True:
                    if self.stop.is_set(): return
                    if self.sender.api.GetForegroundWindow() != target:
                        self.messages.put('Pencere değişti; çalma durduruldu.'); return
                    remaining = start + beat * beat_seconds - time.perf_counter()
                    if remaining <= 0: break
                    if self.stop.wait(min(.02, remaining)): return
                release = list(set(event['up']) & held)
                if release:
                    self.sender.send(release, up=True); held.difference_update(release)
                down = list(dict.fromkeys(event['down']))
                normal = [scan for scan, shift in down if not shift]
                shifted = [scan for scan, shift in down if shift]
                # Retrigger notes that reuse the same physical key.
                retrigger = list({scan for scan, _ in down} & held)
                if retrigger:
                    self.sender.send(retrigger, up=True); held.difference_update(retrigger)
                if normal:
                    held.update(normal); self.sender.send(normal)
                if shifted:
                    if normal and self.stop.wait(.012): return
                    held.add(42); self.sender.send([42])
                    if self.stop.wait(.012): return
                    held.update(shifted); self.sender.send(shifted)
                    if self.stop.wait(.012): return
                    self.sender.send([42], up=True); held.discard(42)
                self.messages.put(f'Zamanlı çalma: {index+1}/{len(schedule)}')
            self.messages.put('Tamamlandı.')
        except Exception as e: self.messages.put(f'Hata: {e}')
        finally:
            if held:
                try: self.sender.send(list(held),up=True)
                except Exception as e: self.messages.put(f'Tuş bırakma hatası: {e}')
            if self.stop.is_set(): self.messages.put('Durduruldu.')

    def poll(self):
        for vk, action in [(0x77,self.stop.set),(0x75,self.start)]:
            down=bool(self.sender.api.GetAsyncKeyState(vk)&0x8000)
            if down and not self.previous.get(vk): action()
            self.previous[vk]=down
        while not self.messages.empty(): self.status.set(self.messages.get_nowait())
        self.root.after(30,self.poll)
    def close(self):
        self.stop.set()
        if self.worker: self.worker.join(timeout=1)
        self.root.destroy()

if __name__ == '__main__':
    if sys.platform != 'win32': raise SystemExit('Bu program Windows içindir.')
    root=tk.Tk(); App(root); root.mainloop()
