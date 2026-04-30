#!/usr/bin/env python3
"""Simple test to verify the filter changes."""

import re

def test_filter_changes():
    """Test that the filter changes are correct."""
    
    # Read the modified file
    with open('/Users/Admin/Desktop/TienAmCac/backend/agents/video_review_engine.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check that vertical filter has been updated
    # Should contain boxblur and overlay for vertical orientation
    vertical_filter_pattern = r'if orientation == VideoOrientation\.VERTICAL:.*?boxblur.*?overlay'
    
    # Find the vertical filter section
    vertical_section_start = content.find('For VERTICAL video, use smart auto-tracking crop with blurred background')
    if vertical_section_start == -1:
        print("[FAIL] Could not find updated vertical filter comment")
        return False
    
    # Get the section (next 500 chars)
    vertical_section = content[vertical_section_start:vertical_section_start + 1000]
    
    # Check for required components
    checks = [
        ('boxblur', 'Vertical filter should contain boxblur for background'),
        ('overlay', 'Vertical filter should contain overlay for foreground'),
        ('scale=1080:1920', 'Vertical filter should scale to 1080x1920'),
        ('crop=1080:1920', 'Vertical filter should have 1080:1920 crop in background'),
    ]
    
    all_passed = True
    for check_str, description in checks:
        if check_str in vertical_section:
            print("[PASS] " + description)
        else:
            print("[FAIL] " + description)
            all_passed = False
    
    # Check that horizontal filter is unchanged
    horizontal_section_start = content.find('if orientation == VideoOrientation.HORIZONTAL:')
    if horizontal_section_start == -1:
        print("[FAIL] Could not find horizontal filter")
        return False
    
    horizontal_section = content[horizontal_section_start:horizontal_section_start + 500]
    
    if 'scale=1920:1080' in horizontal_section:
        print("[PASS] Horizontal filter scales to 1920x1080 (unchanged)")
    else:
        print("[FAIL] Horizontal filter may have been modified incorrectly")
        all_passed = False
    
    return all_passed

def test_select_visual_ranges_changes():
    """Test that select_visual_ranges has been improved."""
    
    with open('/Users/Admin/Desktop/TienAmCac/backend/agents/video_review_engine.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Find the function
    func_start = content.find('def select_visual_ranges(')
    if func_start == -1:
        print("[FAIL] Could not find select_visual_ranges function")
        return False
    
    # Get the full function (until next function definition)
    func_end = content.find('\n    def ', func_start + 10)
    if func_end == -1:
        func_end = len(content)
    func_section = content[func_start:func_end]
    
    # Check for improvements
    checks = [
        ('target_time', 'Should calculate target time for even distribution'),
        ('time_interval', 'Should calculate time interval for distribution'),
        ('importance', 'Should calculate importance score'),
        ('time_penalty', 'Should penalize distance from target time'),
    ]
    
    all_passed = True
    for check_str, description in checks:
        if check_str in func_section:
            print("[PASS] " + description)
        else:
            print("[FAIL] " + description)
            all_passed = False
    
    # Check that old simple sorting is removed
    if 'key=lambda item: (item.get("words", 0), item["end"] - item["start"])' in func_section:
        print("[FAIL] Old simple sorting still present (should be replaced)")
        all_passed = False
    else:
        print("[PASS] Old simple sorting has been replaced")
    
    return all_passed

def main():
    print("=" * 60)
    print("Testing Video Review Engine Fixes")
    print("=" * 60)
    print()
    
    print("1. Checking vertical filter improvements...")
    print("-" * 60)
    filter_ok = test_filter_changes()
    print()
    
    print("2. Checking select_visual_ranges improvements...")
    print("-" * 60)
    ranges_ok = test_select_visual_ranges_changes()
    print()
    
    print("=" * 60)
    if filter_ok and ranges_ok:
        print("All checks passed!")
        print("=" * 60)
        return 0
    else:
        print("Some checks failed!")
        print("=" * 60)
        return 1

if __name__ == "__main__":
    import sys
    sys.exit(main())
