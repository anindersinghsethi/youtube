import os

# Run this script to create/update all files in the repository, including app.py
# After running:
#   pip install -r requirements.txt
#   streamlit run app.py

def update_instruction():
    print("Save the updated requirements.txt, src/aligner.py, and app.py above, then launch with:")
    print("streamlit run app.py")

if __name__ == "__main__":
    update_instruction()