#!/usr/bin/env python3
"""
CLI tool for exploring NetCDF files.
List variables, inspect metadata, and view data.
"""

import argparse
import sys
import xarray as xr
import numpy as np
from pathlib import Path


def open_nc_file(file_path: str) -> xr.Dataset:
    """Open a NetCDF file and return the dataset."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    return xr.open_dataset(path)


def list_variables(ds: xr.Dataset) -> list:
    """Return a sorted list of all variables in the dataset."""
    return sorted(ds.variables.keys())


def get_variable_info(ds: xr.Dataset, var_name: str) -> dict:
    """Get metadata about a specific variable."""
    if var_name not in ds.variables:
        raise KeyError(f"Variable '{var_name}' not found in dataset")
    
    var = ds[var_name]
    return {
        'name': var_name,
        'dims': var.dims,
        'shape': var.shape,
        'dtype': str(var.dtype),
        'attrs': dict(var.attrs),
    }


def print_variable_list(variables: list):
    """Print the list of variables with indices."""
    print(f"\n{'='*60}")
    print(f"Found {len(variables)} variables:")
    print(f"{'='*60}")
    for i, var in enumerate(variables, 1):
        print(f"  [{i:3d}] {var}")
    print(f"{'='*60}\n")


def print_variable_info(info: dict):
    """Print formatted variable metadata."""
    print(f"\n{'─'*60}")
    print(f"Variable: {info['name']}")
    print(f"{'─'*60}")
    print(f"  Dimensions: {info['dims']}")
    print(f"  Shape:      {info['shape']}")
    print(f"  Data type:  {info['dtype']}")
    if info['attrs']:
        print(f"  Attributes:")
        for key, val in info['attrs'].items():
            print(f"    {key}: {val}")
    print(f"{'─'*60}\n")


def print_variable_data(ds: xr.Dataset, var_name: str, max_rows: int = 50):
    """Print the data for a variable."""
    var = ds[var_name]
    data = var.values
    
    print(f"\nData for '{var_name}':")
    print(f"{'─'*60}")
    
    flat = data.flatten()
    total = len(flat)
    
    if total <= max_rows:
        print(data)
    else:
        print(f"(Showing first {max_rows} of {total} values)")
        print(flat[:max_rows])
        print(f"... ({total - max_rows} more values)")
    
    print(f"\nStatistics:")
    if np.issubdtype(data.dtype, np.number):
        valid = flat[~np.isnan(flat)] if np.issubdtype(data.dtype, np.floating) else flat
        print(f"  Min:  {np.min(valid)}")
        print(f"  Max:  {np.max(valid)}")
        print(f"  Mean: {np.mean(valid):.6f}")
        print(f"  Std:  {np.std(valid):.6f}")
        if np.issubdtype(data.dtype, np.floating):
            nan_count = np.sum(np.isnan(flat))
            print(f"  NaN count: {nan_count}")
    print(f"{'─'*60}\n")


def interactive_mode(ds: xr.Dataset):
    """Run interactive variable explorer."""
    variables = list_variables(ds)
    
    while True:
        print_variable_list(variables)
        print("Enter a number to select a variable, or:")
        print("  'q' to quit")
        print("  's <term>' to search variables")
        print("  'a' to show all variables again")
        
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break
        
        if not user_input:
            continue
        
        if user_input.lower() == 'q':
            print("Goodbye!")
            break
        
        if user_input.lower() == 'a':
            continue
        
        if user_input.lower().startswith('s '):
            search_term = user_input[2:].strip().lower()
            matches = [v for v in variables if search_term in v.lower()]
            if matches:
                print(f"\nVariables matching '{search_term}':")
                for i, var in enumerate(matches, 1):
                    idx = variables.index(var) + 1
                    print(f"  [{idx:3d}] {var}")
            else:
                print(f"No variables match '{search_term}'")
            continue
        
        try:
            idx = int(user_input)
            if 1 <= idx <= len(variables):
                var_name = variables[idx - 1]
                info = get_variable_info(ds, var_name)
                print_variable_info(info)
                
                show_data = input("Show data? [y/N]: ").strip().lower()
                if show_data == 'y':
                    print_variable_data(ds, var_name)
            else:
                print(f"Please enter a number between 1 and {len(variables)}")
        except ValueError:
            print(f"Invalid input: '{user_input}'")


def main():
    parser = argparse.ArgumentParser(
        description="Explore NetCDF files - list variables and inspect data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s data.nc                    # Interactive mode
  %(prog)s data.nc --list             # List all variables
  %(prog)s data.nc --var temperature  # Show info for 'temperature'
  %(prog)s data.nc --var temperature --data  # Show data too
        """
    )
    
    parser.add_argument("file", help="Path to NetCDF file")
    parser.add_argument("-l", "--list", action="store_true",
                        help="List all variables and exit")
    parser.add_argument("-v", "--var", metavar="NAME",
                        help="Show info for a specific variable")
    parser.add_argument("-d", "--data", action="store_true",
                        help="Show variable data (use with --var)")
    parser.add_argument("--max-rows", type=int, default=50,
                        help="Max rows to display (default: 50)")
    
    args = parser.parse_args()
    
    try:
        ds = open_nc_file(args.file)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error opening file: {e}", file=sys.stderr)
        sys.exit(1)
    
    try:
        if args.list:
            variables = list_variables(ds)
            print_variable_list(variables)
        
        elif args.var:
            try:
                info = get_variable_info(ds, args.var)
                print_variable_info(info)
                if args.data:
                    print_variable_data(ds, args.var, args.max_rows)
            except KeyError as e:
                print(f"Error: {e}", file=sys.stderr)
                print("Use --list to see available variables")
                sys.exit(1)
        
        else:
            interactive_mode(ds)
    
    finally:
        ds.close()


if __name__ == "__main__":
    main()
