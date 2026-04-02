#!/usr/bin/env python3
"""
Update INTERCEPT values and OC-specific parameters in msgraph.yaml.
Usage:
    python3 update_intercept.py <method> [--yaml-path /path/to/msgraph.yaml] [--params key1=val1 key2=val2 ...]
    
    method: rajomon | breakwater | breakwaterd | dagor | topdown | plain
    
Examples:
    python3 update_intercept.py rajomon
    python3 update_intercept.py breakwater --params BREAKWATER_SLO=15000us BREAKWATER_A=0.002
    python3 update_intercept.py rajomon --yaml-path /path/to/hotelApp/msgraph.yaml
"""

import sys
import argparse
import copy

import yaml


def update_intercept(yaml_path, method, param_overrides=None):
    """Update all nodes' INTERCEPT value and optional parameter overrides."""
    with open(yaml_path, 'r') as f:
        data = yaml.safe_load(f)
    
    if 'nodes' not in data:
        print("Error: 'nodes' key not found in YAML", file=sys.stderr)
        sys.exit(1)
    
    for node in data['nodes']:
        if 'rajomon' not in node:
            continue
        
        # Update INTERCEPT value
        for cfg in node['rajomon']:
            if cfg['name'] == 'INTERCEPT':
                cfg['value'] = method
        
        # Apply parameter overrides
        if param_overrides:
            for cfg in node['rajomon']:
                if cfg['name'] in param_overrides:
                    cfg['value'] = param_overrides[cfg['name']]
    
    with open(yaml_path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    
    print(f"Updated all nodes INTERCEPT={method} in {yaml_path}")
    if param_overrides:
        print(f"  Overrides: {param_overrides}")


def main():
    parser = argparse.ArgumentParser(description='Update INTERCEPT in msgraph.yaml')
    parser.add_argument('method', choices=['rajomon', 'breakwater', 'breakwaterd', 'dagor', 'topdown', 'plain'])
    # Default: msgraph.yaml in the hotelApp directory (two levels up from this script)
    default_yaml = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'msgraph.yaml')
    parser.add_argument('--yaml-path', default=default_yaml,
                        help='Path to msgraph.yaml')
    parser.add_argument('--params', nargs='*', default=[],
                        help='Parameter overrides (KEY=VALUE format)')
    
    args = parser.parse_args()
    
    param_overrides = {}
    for p in args.params:
        if '=' not in p:
            print(f"Error: Invalid param format '{p}', expected KEY=VALUE", file=sys.stderr)
            sys.exit(1)
        key, value = p.split('=', 1)
        param_overrides[key] = value
    
    update_intercept(args.yaml_path, args.method, param_overrides if param_overrides else None)


if __name__ == '__main__':
    main()
