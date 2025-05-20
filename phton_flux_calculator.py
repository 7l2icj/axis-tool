#!/usr/bin/env python3
"""
Photon Flux Calculator with Diode Models and Current Unit Selection
Based on Kraft et al., Rev. Sci. Instrum. 80, 103302 (2009)
"""

import argparse
import sys
import tkinter as tk
from tkinter import ttk, messagebox
import math

# Constants
E_CHARGE = 1.6022e-19  # Electronic charge in Coulombs
E_PAIR = 3.66  # Energy to generate electron-hole pair in eV
E_PAIR_ERROR = 0.03  # Error in e_pair

# Current unit conversion factors to Amperes
CURRENT_UNITS = {
    "mA": 1e-3,
    "μA": 1e-6,
    "nA": 1e-9
}

# Diode database with typical quantum efficiencies and thickness
DIODE_DATABASE = {
    "AXUV100": {
        "manufacturer": "IRD",
        "thickness_um": 100,
        "qe_range": {"8keV": 0.95, "12keV": 0.85, "20keV": 0.60},
        "description": "100μm Si photodiode, high QE"
    },
    "AXUV100G": {
        "manufacturer": "IRD",
        "thickness_um": 100,
        "qe_range": {"8keV": 0.96, "12keV": 0.87, "20keV": 0.62},
        "description": "100μm Si photodiode with AR coating"
    },
    "AXUV20HS": {
        "manufacturer": "IRD",
        "thickness_um": 20,
        "qe_range": {"8keV": 0.85, "12keV": 0.65, "20keV": 0.30},
        "description": "20μm Si photodiode, high speed"
    },
    "S3590-09": {
        "manufacturer": "Hamamatsu",
        "thickness_um": 300,
        "qe_range": {"8keV": 0.98, "12keV": 0.95, "20keV": 0.85},
        "description": "300μm Si PIN photodiode"
    },
    "S1223": {
        "manufacturer": "Hamamatsu",
        "thickness_um": 500,
        "qe_range": {"8keV": 0.99, "12keV": 0.97, "20keV": 0.92},
        "description": "500μm Si PIN photodiode"
    },
    "XUV-100": {
        "manufacturer": "OSI Optoelectronics",
        "thickness_um": 100,
        "qe_range": {"8keV": 0.94, "12keV": 0.84, "20keV": 0.58},
        "description": "100μm windowless photodiode"
    },
    "FDS100": {
        "manufacturer": "Thorlabs",
        "thickness_um": 100,
        "qe_range": {"8keV": 0.93, "12keV": 0.83, "20keV": 0.55},
        "description": "100μm Si photodiode"
    },
    "Custom": {
        "manufacturer": "User-defined",
        "thickness_um": None,
        "qe_range": {},
        "description": "User-defined parameters"
    }
}

class PhotonFluxCalculator:
    """Core calculator class for photon flux calculations"""
    
    @staticmethod
    def calculate_flux(energy_keV, current_value, current_unit="mA", qe=1.0, diode_model=None):
        """
        Calculate photon flux from energy and current
        
        Parameters:
        -----------
        energy_keV : float
            Photon energy in keV
        current_value : float
            Measured current value
        current_unit : str
            Current unit ("mA", "μA", or "nA")
        qe : float
            Quantum efficiency (0-1)
        diode_model : str
            Diode model name (optional)
        
        Returns:
        --------
        dict : Dictionary containing flux, error, and other parameters
        """
        if energy_keV <= 0 or current_value <= 0 or qe <= 0:
            raise ValueError("All input values must be positive")
        
        # Convert current to Amperes
        if current_unit not in CURRENT_UNITS:
            raise ValueError(f"Invalid current unit. Must be one of: {list(CURRENT_UNITS.keys())}")
        
        current_A = current_value * CURRENT_UNITS[current_unit]
        energy_eV = energy_keV * 1000
        
        # Calculate photon flux
        flux = (current_A * E_PAIR) / (E_CHARGE * energy_eV * qe)
        
        # Calculate error
        relative_error = E_PAIR_ERROR / E_PAIR
        flux_error = flux * relative_error
        
        # Energy per photon
        energy_per_photon_J = energy_eV * E_CHARGE
        
        result = {
            'flux': flux,
            'flux_error': flux_error,
            'energy_per_photon_J': energy_per_photon_J,
            'flux_sci': f"{flux:.3e}",
            'flux_error_sci': f"{flux_error:.3e}",
            'energy_per_photon_sci': f"{energy_per_photon_J:.3e}",
            'current_A': current_A,
            'current_display': f"{current_value} {current_unit}"
        }
        
        if diode_model and diode_model in DIODE_DATABASE:
            result['diode_info'] = DIODE_DATABASE[diode_model]
        
        return result
    
    @staticmethod
    def estimate_qe(diode_model, energy_keV):
        """Estimate quantum efficiency for a given diode model and energy"""
        if diode_model not in DIODE_DATABASE or diode_model == "Custom":
            return 1.0
        
        qe_data = DIODE_DATABASE[diode_model]["qe_range"]
        
        # Simple interpolation based on available data points
        energies = sorted([(float(k.replace("keV", "")), v) for k, v in qe_data.items()])
        
        if energy_keV <= energies[0][0]:
            return energies[0][1]
        elif energy_keV >= energies[-1][0]:
            return energies[-1][1]
        
        # Linear interpolation
        for i in range(len(energies) - 1):
            if energies[i][0] <= energy_keV <= energies[i+1][0]:
                x1, y1 = energies[i]
                x2, y2 = energies[i+1]
                qe = y1 + (y2 - y1) * (energy_keV - x1) / (x2 - x1)
                return qe
        
        return 1.0

class GUI:
    """GUI interface for photon flux calculator"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Photon Flux Calculator with Units")
        self.root.geometry("800x700")
        
        # Create notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Create tabs
        self.calculator_tab = ttk.Frame(self.notebook)
        self.diode_info_tab = ttk.Frame(self.notebook)
        self.batch_tab = ttk.Frame(self.notebook)
        
        self.notebook.add(self.calculator_tab, text="Calculator")
        self.notebook.add(self.diode_info_tab, text="Diode Information")
        self.notebook.add(self.batch_tab, text="Batch Processing")
        
        self.create_calculator_widgets()
        self.create_diode_info_widgets()
        self.create_batch_widgets()
        
    def create_calculator_widgets(self):
        # Main frame
        main_frame = ttk.Frame(self.calculator_tab, padding="20")
        main_frame.pack(fill='both', expand=True)
        
        # Title
        title = tk.Label(main_frame, text="Photon Flux Calculator", 
                        font=("Arial", 18, "bold"))
        title.pack(pady=(0, 20))
        
        # Input frame
        input_frame = ttk.LabelFrame(main_frame, text="Input Parameters", padding="15")
        input_frame.pack(fill='x', pady=(0, 20))
        
        # Diode selection
        diode_frame = ttk.Frame(input_frame)
        diode_frame.pack(fill='x', pady=5)
        ttk.Label(diode_frame, text="Diode Model:", width=15).pack(side='left')
        
        self.diode_var = tk.StringVar(value="Custom")
        self.diode_combo = ttk.Combobox(diode_frame, textvariable=self.diode_var, 
                                       values=list(DIODE_DATABASE.keys()), width=25)
        self.diode_combo.pack(side='left', padx=10)
        self.diode_combo.bind('<<ComboboxSelected>>', self.on_diode_selected)
        
        # Energy input
        energy_frame = ttk.Frame(input_frame)
        energy_frame.pack(fill='x', pady=5)
        ttk.Label(energy_frame, text="Energy (keV):", width=15).pack(side='left')
        self.energy_var = tk.DoubleVar(value=8.0)
        self.energy_entry = ttk.Entry(energy_frame, textvariable=self.energy_var, width=20)
        self.energy_entry.pack(side='left', padx=10)
        self.energy_entry.bind('<KeyRelease>', lambda e: self.update_qe_from_energy())
        
        # Current input with unit selection
        current_frame = ttk.Frame(input_frame)
        current_frame.pack(fill='x', pady=5)
        ttk.Label(current_frame, text="Current:", width=15).pack(side='left')
        
        current_input_frame = ttk.Frame(current_frame)
        current_input_frame.pack(side='left', padx=10)
        
        self.current_var = tk.DoubleVar(value=1.0)
        self.current_entry = ttk.Entry(current_input_frame, textvariable=self.current_var, width=15)
        self.current_entry.pack(side='left')
        
        self.current_unit_var = tk.StringVar(value="mA")
        self.current_unit_combo = ttk.Combobox(current_input_frame, textvariable=self.current_unit_var, 
                                             values=list(CURRENT_UNITS.keys()), width=5, state="readonly")
        self.current_unit_combo.pack(side='left', padx=5)
        
        # Quantum efficiency
        qe_frame = ttk.Frame(input_frame)
        qe_frame.pack(fill='x', pady=5)
        ttk.Label(qe_frame, text="Quantum Efficiency:", width=15).pack(side='left')
        self.qe_var = tk.DoubleVar(value=1.0)
        qe_container = ttk.Frame(qe_frame)
        qe_container.pack(side='left', padx=10)
        
        self.qe_scale = ttk.Scale(qe_container, from_=0.1, to=1.0, variable=self.qe_var, 
                                 orient=tk.HORIZONTAL, length=150)
        self.qe_scale.pack(side="left")
        
        self.qe_label = ttk.Label(qe_container, text="1.00", width=5)
        self.qe_label.pack(side="left", padx=5)
        
        self.qe_auto_label = ttk.Label(qe_container, text="", foreground="blue")
        self.qe_auto_label.pack(side="left", padx=5)
        
        # Update QE label when scale changes
        self.qe_scale.config(command=lambda v: self.update_qe_label())
        
        # Calculate button
        calc_button = ttk.Button(main_frame, text="Calculate", command=self.calculate)
        calc_button.pack(pady=20)
        
        # Results frame
        results_frame = ttk.LabelFrame(main_frame, text="Results", padding="15")
        results_frame.pack(fill='x')
        
        # Results
        results = [
            ("Photon Flux:", "flux_result", "photons/s"),
            ("Error (±):", "error_result", "photons/s"),
            ("Energy per photon:", "energy_result", "J"),
            ("Current:", "current_result", ""),
            ("Diode:", "diode_result", ""),
        ]
        
        self.result_labels = {}
        for label_text, key, unit in results:
            frame = ttk.Frame(results_frame)
            frame.pack(fill='x', pady=3)
            ttk.Label(frame, text=label_text, width=15).pack(side='left')
            self.result_labels[key] = ttk.Label(frame, text="---", font=("Arial", 11, "bold"))
            self.result_labels[key].pack(side='left')
            if unit:
                ttk.Label(frame, text=unit).pack(side='left', padx=5)
    
    def create_diode_info_widgets(self):
        # Main frame
        info_frame = ttk.Frame(self.diode_info_tab, padding="20")
        info_frame.pack(fill='both', expand=True)
        
        # Title
        title = tk.Label(info_frame, text="Diode Database", font=("Arial", 16, "bold"))
        title.pack(pady=(0, 10))
        
        # Create treeview
        columns = ('Manufacturer', 'Thickness (μm)', 'Description')
        self.diode_tree = ttk.Treeview(info_frame, columns=columns, show='tree headings', height=12)
        
        # Define headings
        self.diode_tree.heading('#0', text='Model')
        self.diode_tree.heading('Manufacturer', text='Manufacturer')
        self.diode_tree.heading('Thickness (μm)', text='Thickness (μm)')
        self.diode_tree.heading('Description', text='Description')
        
        # Configure column widths
        self.diode_tree.column('#0', width=150)
        self.diode_tree.column('Manufacturer', width=150)
        self.diode_tree.column('Thickness (μm)', width=100)
        self.diode_tree.column('Description', width=300)
        
        # Add data
        for model, data in DIODE_DATABASE.items():
            if model != "Custom":
                thickness = str(data['thickness_um']) if data['thickness_um'] else "N/A"
                self.diode_tree.insert('', 'end', text=model, 
                                      values=(data['manufacturer'], thickness, data['description']))
        
        # Pack treeview
        self.diode_tree.pack(fill='both', expand=True, pady=(0, 10))
        
        # QE information
        qe_frame = ttk.LabelFrame(info_frame, text="Quantum Efficiency Information", padding="10")
        qe_frame.pack(fill='x')
        
        qe_text = """Quantum efficiency values are typical estimates for the specified energies.
Actual values may vary depending on specific diode characteristics and conditions.
For precise measurements, refer to manufacturer specifications."""
        
        qe_label = ttk.Label(qe_frame, text=qe_text, wraplength=600)
        qe_label.pack()
        
        # Current units information
        units_frame = ttk.LabelFrame(info_frame, text="Current Units", padding="10")
        units_frame.pack(fill='x', pady=(10, 0))
        
        units_text = """Available current units:
• mA (milliamperes) - default
• μA (microamperes)
• nA (nanoamperes)"""
        
        units_label = ttk.Label(units_frame, text=units_text)
        units_label.pack()
    
    def create_batch_widgets(self):
        # Main frame
        batch_frame = ttk.Frame(self.batch_tab, padding="20")
        batch_frame.pack(fill='both', expand=True)
        
        # Instructions
        instructions = ttk.Label(batch_frame, 
                                text="Enter multiple calculations (one per line):\nFormat: energy_keV, current_value, current_unit, qe, diode_model(optional)", 
                                font=("Arial", 11))
        instructions.pack(pady=(0, 10))
        
        # Text input frame
        input_frame = ttk.Frame(batch_frame)
        input_frame.pack(fill='both', expand=True, pady=(0, 10))
        
        self.batch_text = tk.Text(input_frame, height=8, width=60)
        self.batch_text.pack(side='left', fill='both', expand=True)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(input_frame, command=self.batch_text.yview)
        scrollbar.pack(side='right', fill='y')
        self.batch_text.config(yscrollcommand=scrollbar.set)
        
        # Default example
        self.batch_text.insert('1.0', "8.0, 1.0, mA, 1.0\n12.0, 500, μA, 0.9, AXUV100\n20.0, 200, nA, 0.95, S3590-09")
        
        # Process button
        ttk.Button(batch_frame, text="Process Batch", command=self.process_batch).pack(pady=10)
        
        # Results
        result_frame = ttk.Frame(batch_frame)
        result_frame.pack(fill='both', expand=True)
        
        self.batch_results = tk.Text(result_frame, height=10, width=80, state='disabled')
        self.batch_results.pack(side='left', fill='both', expand=True)
        
        # Scrollbar for results
        result_scrollbar = ttk.Scrollbar(result_frame, command=self.batch_results.yview)
        result_scrollbar.pack(side='right', fill='y')
        self.batch_results.config(yscrollcommand=result_scrollbar.set)
    
    def on_diode_selected(self, event=None):
        """Handle diode selection"""
        diode_model = self.diode_var.get()
        if diode_model != "Custom":
            self.update_qe_from_energy()
    
    def update_qe_from_energy(self):
        """Update QE based on selected diode and energy"""
        try:
            diode_model = self.diode_var.get()
            energy = self.energy_var.get()
            
            if diode_model != "Custom" and diode_model in DIODE_DATABASE:
                estimated_qe = PhotonFluxCalculator.estimate_qe(diode_model, energy)
                self.qe_var.set(estimated_qe)
                self.qe_auto_label.config(text=f"(Auto: {estimated_qe:.3f})")
            else:
                self.qe_auto_label.config(text="")
            
            self.update_qe_label()
        except:
            pass
    
    def update_qe_label(self):
        """Update QE display label"""
        self.qe_label.config(text=f"{self.qe_var.get():.2f}")
    
    def calculate(self):
        try:
            energy = self.energy_var.get()
            current_value = self.current_var.get()
            current_unit = self.current_unit_var.get()
            qe = self.qe_var.get()
            diode_model = self.diode_var.get()
            
            result = PhotonFluxCalculator.calculate_flux(energy, current_value, current_unit, qe, diode_model)
            
            self.result_labels['flux_result'].config(text=result['flux_sci'])
            self.result_labels['error_result'].config(text=result['flux_error_sci'])
            self.result_labels['energy_result'].config(text=result['energy_per_photon_sci'])
            self.result_labels['current_result'].config(text=result['current_display'])
            
            if diode_model == "Custom":
                self.result_labels['diode_result'].config(text="Custom parameters")
            else:
                diode_info = DIODE_DATABASE.get(diode_model, {})
                self.result_labels['diode_result'].config(
                    text=f"{diode_model} ({diode_info.get('manufacturer', 'Unknown')})")
            
        except ValueError as e:
            messagebox.showerror("Error", str(e))
    
    def process_batch(self):
        lines = self.batch_text.get('1.0', 'end').strip().split('\n')
        
        self.batch_results.config(state='normal')
        self.batch_results.delete('1.0', 'end')
        
        header = "Energy (keV) | Current     | QE    | Diode Model | Photon Flux (photons/s)\n"
        header += "-" * 80 + "\n"
        self.batch_results.insert('end', header)
        
        for i, line in enumerate(lines):
            if not line.strip():
                continue
                
            try:
                parts = [p.strip() for p in line.split(',')]
                energy = float(parts[0])
                current_value = float(parts[1])
                current_unit = parts[2] if len(parts) > 2 else "mA"
                qe = float(parts[3]) if len(parts) > 3 else 1.0
                diode_model = parts[4] if len(parts) > 4 else "Custom"
                
                # If diode model is specified but QE is default, estimate QE
                if len(parts) > 4 and qe == 1.0 and diode_model != "Custom":
                    qe = PhotonFluxCalculator.estimate_qe(diode_model, energy)
                
                result = PhotonFluxCalculator.calculate_flux(energy, current_value, current_unit, qe, diode_model)
                
                current_display = f"{current_value:6g} {current_unit}"
                output = f"{energy:11.1f} | {current_display:11} | {qe:5.3f} | {diode_model:11} | {result['flux_sci']}\n"
                self.batch_results.insert('end', output)
                
            except Exception as e:
                self.batch_results.insert('end', f"Error on line {i+1}: {e}\n")
        
        self.batch_results.config(state='disabled')

def cli_mode():
    """Command line interface mode"""
    parser = argparse.ArgumentParser(description='Calculate photon flux from energy and current')
    parser.add_argument('energy', type=float, help='Photon energy in keV')
    parser.add_argument('current', type=float, help='Current value')
    parser.add_argument('--unit', type=str, default="mA", choices=list(CURRENT_UNITS.keys()),
                       help='Current unit (default: mA)')
    parser.add_argument('--qe', type=float, default=None, help='Quantum efficiency (default: 1.0 or auto from diode)')
    parser.add_argument('--diode', type=str, default="Custom", help='Diode model (default: Custom)')
    parser.add_argument('--list-diodes', action='store_true', help='List available diode models')
    parser.add_argument('--verbose', action='store_true', help='Show detailed output')
    
    # Handle list-diodes first
    if '--list-diodes' in sys.argv:
        print("Available diode models:")
        print("-" * 50)
        for model, data in DIODE_DATABASE.items():
            if model != "Custom":
                print(f"{model:12} - {data['manufacturer']:20} - {data['description']}")
        sys.exit(0)
    
    args = parser.parse_args()
    
    try:
        # Auto-determine QE if diode model is specified and QE is not
        qe = args.qe
        if qe is None:
            if args.diode != "Custom" and args.diode in DIODE_DATABASE:
                qe = PhotonFluxCalculator.estimate_qe(args.diode, args.energy)
            else:
                qe = 1.0
        
        result = PhotonFluxCalculator.calculate_flux(args.energy, args.current, args.unit, qe, args.diode)
        
        if args.verbose:
            print(f"Photon Flux Calculator Results")
            print(f"=" * 30)
            print(f"Input Parameters:")
            print(f"  Energy: {args.energy} keV")
            print(f"  Current: {args.current} {args.unit}")
            print(f"  Quantum Efficiency: {qe:.3f}")
            print(f"  Diode Model: {args.diode}")
            if args.diode != "Custom" and args.diode in DIODE_DATABASE:
                diode_info = DIODE_DATABASE[args.diode]
                print(f"  Manufacturer: {diode_info['manufacturer']}")
                print(f"  Thickness: {diode_info['thickness_um']} μm")
            print(f"\nResults:")
            print(f"  Photon Flux: {result['flux_sci']} photons/s")
            print(f"  Error: ±{result['flux_error_sci']} photons/s")
            print(f"  Energy per photon: {result['energy_per_photon_sci']} J")
        else:
            print(f"{result['flux_sci']} photons/s")
            
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyError:
        print(f"Error: Unknown diode model '{args.diode}'", file=sys.stderr)
        print("Use --list-diodes to see available models", file=sys.stderr)
        sys.exit(1)

def main():
    """Main entry point"""
    if len(sys.argv) > 1 and not sys.argv[1].startswith('--'):
        # CLI mode
        cli_mode()
    else:
        # GUI mode
        root = tk.Tk()
        app = GUI(root)
        root.mainloop()

if __name__ == "__main__":
    main()