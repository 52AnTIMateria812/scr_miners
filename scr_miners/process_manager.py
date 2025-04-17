import ctypes
import json
import tkinter as tk
from tkinter import ttk, messagebox
import sys
import os
import psutil
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import time
from collections import OrderedDict
import timeit

DEBUG = True

class ProcessCache:
    def __init__(self, max_size=500):
        self._cache = OrderedDict()
        self.max_size = max_size
        self._last_update = 0
        self._static_fields = {'pid', 'name', 'username', 'exe', 'cwd', 'create_time'}
        self._dynamic_fields = {'memory_kb', 'cpu_percent', 'status', 'num_threads'}
    
    def update(self, processes):
        current_time = time.time()
        new_cache = OrderedDict()
        
        for proc in processes:
            pid = proc['pid']
            
            if pid in self._cache and current_time - self._last_update < 2.0:
                cached = self._cache[pid]
                for field in self._static_fields:
                    if field in cached:
                        proc[field] = cached[field]
            
            new_cache[pid] = proc
            if len(new_cache) >= self.max_size:
                break
        
        self._cache = new_cache
        self._last_update = current_time
        return processes
    
    def needs_full_update(self, current_pids):
        cached_pids = set(self._cache.keys())
        new_pids = current_pids - cached_pids
        gone_pids = cached_pids - current_pids
        
        if len(new_pids) > 5 or len(gone_pids) > 5:
            return True
        
        return time.time() - self._last_update > 5.0

class ProcessManager:
    def __init__(self):
        self.processes = []
        self.cache = ProcessCache()
        self.last_full_update = 0
        
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            dll_path = os.path.join(script_dir, r'./ProcessInfo.dll') 
            
            self.dll = ctypes.WinDLL(dll_path)
            
            # Определение типов функций
            self.dll.GetProcessesInfo.restype = ctypes.c_char_p
            self.dll.GetProcessesInfo.argtypes = []
            
            self.dll.FreeProcessInfoMemory.argtypes = [ctypes.c_char_p]
            self.dll.FreeProcessInfoMemory.restype = None
        except Exception as e:
            print(f"Ошибка загрузки DLL: {e}")
            self.dll = None
    
    def get_processes_from_dll(self):
        if not self.dll:
            return []
            
        try:
            processes_json = self.dll.GetProcessesInfo()
            if not processes_json:
                return []
                
            processes = json.loads(processes_json.decode('utf-8'))
            
            self.dll.FreeProcessInfoMemory(processes_json)
            
            return processes
        except Exception as e:
            print(f"Ошибка получения данных из DLL: {e}")
            return []
    
    def get_processes(self, full_update=False):
        start_time = timeit.default_timer()
        
        if self.dll and full_update:
            dll_processes = self.get_processes_from_dll()
            if dll_processes:
                self.processes = []
                for proc in dll_processes:
                    try:
                        new_proc = {
                            'pid': proc['pid'],
                            'name': proc['name'],
                            'memory_kb': proc['memory_kb'],
                            'cpu_percent': 0.0,  
                            'status': 'running',  
                            'username': 'N/A'     
                        }
                        
                        # Попробуем получить дополнительные данные через psutil
                        try:
                            psutil_proc = psutil.Process(proc['pid'])
                            new_proc.update({
                                'cpu_percent': psutil_proc.cpu_percent(),
                                'status': psutil_proc.status(),
                                'username': psutil_proc.username() or 'N/A'
                            })
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                            
                        self.processes.append(new_proc)
                    except Exception as e:
                        print(f"Ошибка обработки процесса из DLL: {e}")
                        continue
                
                self.processes = self.cache.update(self.processes)
                self.last_full_update = time.time()
                
                if DEBUG:
                    print(f"DLL update in {(timeit.default_timer() - start_time)*1000:.2f}ms")
                return self.processes
        
        if not full_update:
            self._refresh_dynamic_data()
            if DEBUG:
                print(f"Quick update in {(timeit.default_timer() - start_time)*1000:.2f}ms")
            return self.processes
        
        self.processes = []
        for proc in psutil.process_iter(['pid', 'name', 'memory_info', 'cpu_percent', 'status', 'username']):
            try:
                self.processes.append({
                    'pid': proc.info['pid'],
                    'name': proc.info['name'],
                    'memory_kb': proc.info['memory_info'].rss // 1024,
                    'cpu_percent': proc.info['cpu_percent'],
                    'status': proc.info['status'],
                    'username': proc.info['username'] or 'N/A'
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        self.processes = self.cache.update(self.processes)
        self.last_full_update = time.time()
        
        if DEBUG:
            print(f"Full update in {(timeit.default_timer() - start_time)*1000:.2f}ms")
        return self.processes
    
    def _refresh_dynamic_data(self):
        for proc in self.processes:
            try:
                p = psutil.Process(proc['pid'])
                proc.update({
                    'memory_kb': p.memory_info().rss // 1024,
                    'cpu_percent': p.cpu_percent(),
                    'status': p.status()
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    
    def kill_process(self, pid):
        try:
            process = psutil.Process(pid)
            process.terminate()
            return True
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось завершить процесс: {e}")
            return False

class TaskManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Улучшенный диспетчер задач")
        self.geometry("1200x700")
        self.process_manager = ProcessManager()
        self.sort_column = None
        self.sort_reverse = False
        self.memory_history = []
        self.cpu_history = []
        self.update_time = time.time()
        self.last_full_refresh = 0
        
        self.setup_ui()
        self.refresh_processes(full_refresh=True)
        self.after(2000, self.periodic_refresh)

    def setup_ui(self):
        main_frame = ttk.Frame(self)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill="x", pady=(0, 10))
        
        btn_frame = ttk.Frame(control_frame)
        btn_frame.pack(side="left")
        
        ttk.Button(btn_frame, text="Обновить", command=lambda: self.refresh_processes(full_refresh=True)).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="Завершить", command=self.kill_selected_process).pack(side="left", padx=2)
        
        filter_frame = ttk.Frame(control_frame)
        filter_frame.pack(side="right")
        
        ttk.Label(filter_frame, text="Фильтр:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(filter_frame, textvariable=self.filter_var, width=30)
        self.filter_entry.pack(side="left", padx=5)
        self.filter_entry.bind("<KeyRelease>", lambda e: self.refresh_processes())
        
        stats_frame = ttk.Frame(main_frame)
        stats_frame.pack(fill="x", pady=(0, 10))
        
        cpu_frame = ttk.Frame(stats_frame)
        cpu_frame.pack(side="left", fill="x", expand=True)
        ttk.Label(cpu_frame, text="Использование CPU (%)").pack()
        self.cpu_fig = Figure(figsize=(5, 2), dpi=100)
        self.cpu_ax = self.cpu_fig.add_subplot(111)
        self.cpu_canvas = FigureCanvasTkAgg(self.cpu_fig, master=cpu_frame)
        self.cpu_canvas.get_tk_widget().pack(fill="x")
        
        mem_frame = ttk.Frame(stats_frame)
        mem_frame.pack(side="left", fill="x", expand=True)
        ttk.Label(mem_frame, text="Использование памяти (MB)").pack()
        self.mem_fig = Figure(figsize=(5, 2), dpi=100)
        self.mem_ax = self.mem_fig.add_subplot(111)
        self.mem_canvas = FigureCanvasTkAgg(self.mem_fig, master=mem_frame)
        self.mem_canvas.get_tk_widget().pack(fill="x")
        
        tree_frame = ttk.Frame(main_frame)
        tree_frame.pack(fill="both", expand=True)
        
        self.tree = ttk.Treeview(tree_frame, columns=("PID", "Name", "Memory", "CPU", "Status", "User"), show="headings")
        
        columns = {
            "PID": {"width": 80, "anchor": "center"},
            "Name": {"width": 300},
            "Memory": {"width": 100, "anchor": "e"},
            "CPU": {"width": 80, "anchor": "e"},
            "Status": {"width": 100},
            "User": {"width": 150}
        }
        
        for col, settings in columns.items():
            self.tree.heading(col, text=col, command=lambda c=col: self.sort_by_column(c))
            self.tree.column(col, **settings)
        
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        self.setup_context_menu()  
        
        self.system_info_label = ttk.Label(main_frame, text="")
        self.system_info_label.pack(fill="x", pady=(5, 0))
        
        self.update_system_info()

    def setup_context_menu(self): 
        """Настройка контекстного меню для дерева процессов"""
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Подробности", command=self.show_process_details)
        self.context_menu.add_command(label="Завершить процесс", command=self.kill_selected_process)
        
        self.tree.bind("<Button-3>", self.show_context_menu)

    def show_context_menu(self, event):
        """Показать контекстное меню"""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            try:
                self.context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.context_menu.grab_release()

    def kill_selected_process(self):
        """Завершение выбранного процесса"""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Предупреждение", "Выберите процесс для завершения")
            return
            
        item = self.tree.item(selected[0])
        try:
            pid = int(item['values'][0])
            name = item['values'][1]
        except (IndexError, ValueError):
            messagebox.showerror("Ошибка", "Не удалось получить данные процесса")
            return
            
        if messagebox.askyesno("Подтверждение", 
                             f"Вы уверены, что хотите завершить процесс?\nPID: {pid}\nИмя: {name}"):
            if self.process_manager.kill_process(pid):
                self.refresh_processes(full_refresh=True)

    def refresh_processes(self, full_refresh=False):
        """Обновление списка процессов с учетом кэширования"""
        try:
            current_pids = {p['pid'] for p in self.process_manager.processes} if self.process_manager.processes else set()
            
            if full_refresh or not current_pids or self.process_manager.cache.needs_full_update(current_pids):
                processes = self.process_manager.get_processes(full_update=True)
            else:
                processes = self.process_manager.get_processes(full_update=False)
            
            filter_text = self.filter_var.get().strip().lower()
            if filter_text:
                processes = [p for p in processes if 
                           filter_text in str(p['pid']).lower() or 
                           filter_text in p['name'].lower() or
                           filter_text in (p.get('username', 'N/A') or 'N/A').lower()]
            
            if self.sort_column:
                self._sort_processes(processes)
            
            self.update_charts()
            self.update_treeview(processes)
        except Exception as e:
            if DEBUG:
                print(f"Ошибка в refresh_processes: {e}")

    def _sort_processes(self, processes):
        """Внутренний метод для сортировки процессов"""
        col_name = self.sort_column.lower()
        reverse = self.sort_reverse
        
        if col_name == 'pid':
            processes.sort(key=lambda x: x['pid'], reverse=reverse)
        elif col_name == 'name':
            processes.sort(key=lambda x: x['name'].lower(), reverse=reverse)
        elif col_name == 'memory':
            processes.sort(key=lambda x: x['memory_kb'], reverse=reverse)
        elif col_name == 'cpu':
            processes.sort(key=lambda x: x['cpu_percent'], reverse=reverse)
        elif col_name == 'status':
            processes.sort(key=lambda x: x['status'], reverse=reverse)
        elif col_name == 'user':
            processes.sort(key=lambda x: x.get('username', 'N/A'), reverse=reverse)

    def update_treeview(self, processes):
        """Оптимизированное обновление Treeview"""
        try:
            first_visible = self.tree.yview()[0]
            selected = self.tree.selection()
            selected_pid = int(self.tree.item(selected[0])['values'][0]) if selected else None
            
            self.tree.delete(*self.tree.get_children())
            for proc in processes:
                try:
                    item = self.tree.insert("", "end", values=(
                        proc['pid'],
                        proc['name'],
                        f"{proc['memory_kb']:,}",
                        f"{proc['cpu_percent']:.1f}",
                        proc['status'],
                        proc.get('username', 'N/A')
                    ))
                    
                    if selected_pid and proc['pid'] == selected_pid:
                        self.tree.selection_set(item)
                except Exception as e:
                    if DEBUG:
                        print(f"Ошибка при добавлении процесса {proc.get('pid')}: {e}")
            
            self.tree.yview_moveto(first_visible)
        except Exception as e:
            if DEBUG:
                print(f"Ошибка в update_treeview: {e}")

    def update_system_info(self):
        """Обновление общей информации о системе"""
        try:
            cpu_percent = psutil.cpu_percent()
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            net = psutil.net_io_counters()
            
            text = (f"CPU: {cpu_percent}% | "
                    f"Память: {mem.used / 1024 / 1024:.0f} MB / {mem.total / 1024 / 1024:.0f} MB ({mem.percent}%) | "
                    f"Диск: {disk.used / 1024 / 1024 / 1024:.1f} GB / {disk.total / 1024 / 1024 / 1024:.1f} GB | "
                    f"Сеть: ↑ {net.bytes_sent / 1024 / 1024:.1f} MB ↓ {net.bytes_recv / 1024 / 1024:.1f} MB")
            
            self.system_info_label.config(text=text)
        except Exception as e:
            if DEBUG:
                print(f"Ошибка в update_system_info: {e}")
        finally:
            self.after(1000, self.update_system_info)

    def periodic_refresh(self):
        """Периодическое обновление списка процессов"""
        self.refresh_processes()
        self.after(2000, self.periodic_refresh)

    def update_charts(self):
        """Обновление графиков использования ресурсов"""
        now = time.time()
        if now - self.update_time < 1:
            return
        
        self.update_time = now
        
        try:
            cpu_percent = psutil.cpu_percent()
            self.cpu_history.append(cpu_percent)
            if len(self.cpu_history) > 60:
                self.cpu_history.pop(0)
            
            self.cpu_ax.clear()
            self.cpu_ax.plot(self.cpu_history, 'r-')
            self.cpu_ax.set_ylim(0, 100)
            self.cpu_ax.set_title(f"CPU: {cpu_percent:.1f}%")
            self.cpu_canvas.draw()
            
            mem = psutil.virtual_memory()
            mem_percent = mem.percent
            used_mb = mem.used / 1024 / 1024
            self.memory_history.append(used_mb)
            if len(self.memory_history) > 60:
                self.memory_history.pop(0)
            
            self.mem_ax.clear()
            self.mem_ax.plot(self.memory_history, 'b-')
            self.mem_ax.set_title(f"Память: {used_mb:.0f} MB ({mem_percent:.1f}%)")
            self.mem_canvas.draw()
        except Exception as e:
            if DEBUG:
                print(f"Ошибка в update_charts: {e}")

    def show_process_details(self):
        """Показать детальную информацию о выбранном процессе"""
        selected = self.tree.selection()
        if not selected:
            return
            
        item = self.tree.item(selected[0])
        pid = int(item['values'][0])
        
        try:
            process = psutil.Process(pid)
            info = {
                "PID": pid,
                "Имя": process.name(),
                "Статус": process.status(),
                "Пользователь": process.username(),
                "Путь": process.exe() or "N/A",
                "Рабочая директория": process.cwd(),
                "Запущен": time.ctime(process.create_time()),
                "Потоки": process.num_threads(),
                "Память (RSS)": f"{process.memory_info().rss / 1024 / 1024:.2f} MB",
                "Память (VMS)": f"{process.memory_info().vms / 1024 / 1024:.2f} MB",
                "CPU %": f"{process.cpu_percent():.1f}%",
                "Открытые файлы": len(process.open_files()),
                "Соединения": len(process.connections())
            }
            
            details = "\n".join(f"{k}: {v}" for k, v in info.items())
            messagebox.showinfo(f"Детали процесса {pid}", details)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось получить информацию о процессе: {e}")

    def sort_by_column(self, column):
        """Сортировка по выбранной колонке"""
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        
        self.refresh_processes()

if __name__ == "__main__":
    try:
        import psutil
        from matplotlib import pyplot as plt
        
        app = TaskManagerApp()
        app.mainloop()
    except ImportError as e:
        messagebox.showerror("Ошибка", f"Не удалось импортировать необходимые модули: {e}")
        sys.exit(1)
    except Exception as e:
        messagebox.showerror("Критическая ошибка", f"Не удалось запустить приложение: {e}")
        sys.exit(1)
        
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            dll_path = os.path.join(script_dir, 'ProcessInfo.dll')
            if not os.path.exists(dll_path):
                print("Предупреждение: ProcessInfo.dll не найдена. Будут использоваться только функции psutil.")
        except:
            pass
            
        app = TaskManagerApp()
        app.mainloop()
    except ImportError as e:
        messagebox.showerror("Ошибка", f"Не удалось импортировать необходимые модули: {e}")
        sys.exit(1)
    except Exception as e:
        messagebox.showerror("Критическая ошибка", f"Не удалось запустить приложение: {e}")
        sys.exit(1)