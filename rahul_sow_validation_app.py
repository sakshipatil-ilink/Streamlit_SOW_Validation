import streamlit as st
import tempfile
from snowflake.snowpark.context import get_active_session
import json
import pandas as pd
from snowflake.cortex import Complete
from PyPDF2 import PdfReader
from docx import Document
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
import os
import re

session = get_active_session()

st.set_page_config(page_title="SOW Validation", layout="wide")
st.title("SOW Validation")  


def query_cortex_search_service(query, target_section_name):
    json_payload = json.dumps({
        "query": query,
        "columns": ["chunk", "section_name"],
          #"filter": {
             # "@eq": { "section_name": target_section_name }
         # },
        "limit": 5
       
    })
    
    # Escape single quotes in JSON payload for SQL
    escaped_payload = json_payload.replace("'", "''")
    
    sql_query = f"""
        SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
            'sow_validation_service_lang_new',
            '{escaped_payload}'
        ) AS search_results
    """

    try:
        results_df = session.sql(sql_query).collect()
        raw_results = results_df[0]["SEARCH_RESULTS"]
        results = json.loads(raw_results)
       

       
        
        return results.get("results", [])
        
    except Exception as e:
        st.error(f"Error querying section {target_section_name}: {e}")
        return []

# def query_cortex_search_for_type(query):
#      json_payload = json.dumps({
#         "query": query,
#         "columns": ["chunk"],
        
#         "limit": 100
#     })
    
#     # Escape single quotes in JSON payload for SQL
#      escaped_payload = json_payload.replace("'", "''")
    
#      sql_query = f"""
#         SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
#             'sow_type_identification',
#             '{escaped_payload}'
#         ) AS search_results
#     """

#      try:
#         results_df = session.sql(sql_query).collect()
#         raw_results = results_df[0]["SEARCH_RESULTS"]
#         results = json.loads(raw_results)
#         return results.get("results", [])
#      except Exception as e:
#         st.error(f"Error querying section Compensation: {e}")
#         return []


def get_available_sections_mapping():
    """Get mapping between config section names and actual database section names"""
    sections_df = session.sql("""
        SELECT DISTINCT section_name
        FROM doc_chunks_sow
        ORDER BY section_name
    """).collect()
    
    db_sections = [row['SECTION_NAME'] for row in sections_df]
    
    # Create mapping between config names and database section names
    section_mapping = {}
    
    for config_section in [ "Header", "SOW Term", "Scope of Services","Compensation", "Project Assumptions", "McKesson Responsibilities", "Change Control Procedure", "Financial Information", 
                          "PII or PHI", "Sensitive Information", "Access", 
                          "Artificial Intelligence", "Exhibits", "Deliverables, Milestones and Compensation", "Statement of Work Characteristic", "McKesson Change Order Template", "Additional Terms", "Project Oversight"]:
        
        # Find matching section in database (case-insensitive, partial match)
        matched_section = None
        for db_section in db_sections:
            if config_section.lower() in db_section.lower() or db_section.lower() in config_section.lower():
                matched_section = db_section
                break
            
            # Special cases for common variations
            if config_section == "PII or PHI" and ("pii" in db_section.lower() or "phi" in db_section.lower()):
                matched_section = db_section
                break
          
            elif config_section == "Financial Information" and "financial" in db_section.lower():
                matched_section = db_section
                break
        
        if matched_section:
            section_mapping[config_section] = matched_section
        else:
            st.warning(f"No matching section found for: {config_section}")
    
    return section_mapping

def get_validation_config_by_sow_type(sow_type):
    """
    Returns the appropriate validation configuration based on SOW type
    
    Args:
        sow_type (str): The type of SOW - "T&M" or "Fixed-Fee"
    
    Returns:
        dict: Validation configuration dictionary for the specified SOW type
    """
    
    # Time & Materials validation configuration
    TM_validation_config = {
        "Header": {
            "search_query": "Retrieve the Header section of the SOW containing Supplier Name, SOW Start Date/Effective Date, Client Name, MSA Start Date",
            "validation_questions": [
                "Supplier Name: Does the header section contain a supplier name, and is it consistent with other contract documents?",
                "SOW Start Date: Does the header section contain a SOW start date? Provide the date if present. Only report issues if dates are missing or if they conflict with other contract documents",
                "Client Name: Does the header section contain a client name, and is it consistent with other contract documents?",
                "MSA Start Date: Does the header section contain a MSA start date? Provide the date if present. If value is provided, SOW start date can be same as MSA date or after the MSA date. Only report issues if dates are missing or if they conflict with other contract documents"
               
            ],

        "tag": "This section is mandatory. If it is not present in the document, assign high severity."
        },
        "SOW Term": {
            "search_query": "Retrieve the SOW Term section containing SOW End Date/SOW Completion Date",
            "validation_questions": [
                "SOW End date: Does the sow term section contain a SOW end date/SOW completion date? Provide the date if present. If value is provided, sow end date should be after the sow start date. Only report issues if dates are missing or they are before the sow start date or if they conflict with other contract documents"
                
               
            ],
             "tag": "This section is mandatory. If it is not present in the document, assign high severity."
        },
        "Scope of Services": {
            "search_query": "Retrieve all Scope of Services related sections",
            "validation_questions": [
                "Project Overview: Does the scope of services section contain project overview with clear and precise language? If overview present, does it have a project name?",
                "Project Scope: Does the scope of services section contain project scope with clear and precise language?",
                "Out-of-Scope Work: Does the scope of services section contain out-of-scope work with clear and precise language? If it is not present in the section, assign medium severity",
                "Assumptions: Does the scope of services section contain assumptions which are made in preparing the SOW (that contain dependencies and constraints, risks and mitigation strategies)? If assumptions are not mentioned, assign medium severity",         
                "Resource Name: Does the scope of service section contain resource name (Person name who will be working). If it is not mentioned, assign low severity",
                "Role: Does the scope of service section contain role (role title/skill of the resource e.g. BI Developer (Power BI))? If it is not mentioned, assign high severity",
                "Delivery Location: Does the scope of service section contain delivery location (resource location)? If it is not mentioned, assign medium severity",
                "Hourly Rate: Does the scope of service section contain hourly rate (resource hourly rate IS MANDATORY for time & material type SOW but billing rates are optional for fixed bid type SOW)? If it is not mentioned in case of T&M SOW, assign high severity",        
                "Total No Of hours/ No of Hours/Hours: Does the scope of service section contain total no of hours/no of hours/hours? If it is not mentioned, assign high severity.",            
                "Total Cost: Does the scope of service section contain total cost? If it is not mentioned, assign low severity",            
                "Monthly run rate: Does the scope of service section contain monthly rate? If it is not mentioned, assign low severity"
               
           
            ],
            "tag": "This section is mandatory. If it is not present in the document, assign high severity."
        },   
        "Compensation": {
            "search_query": "Retrieve all compensation-related sections, this could include compensation and Deliverables",
            "validation_questions": [
                "Is payment term T&M clearly stated with language Total Not to Exceed Fee basis? If this is not stated clearly, assign high severity",
                "Are deliverable names listed? If not mentioned, assign high severity",
                "Is the fee amount clearly listed? It should have language like Total cost of the project should not exceed $.. If it is not mentioned, assign high severity",     
                "Are invoicing terms - payment schedule, method, invoicing condition, and any exceptions - clearly defined? SOW must have a reference to MSA payment terms called out like payment's terms in the agreement. There should NOT be language like Net60, Net30, payment in 30 days, etc phrases. If it is not clearly stated, assign high severity"
                
               
            ],
            "tag":"This section is mandatory. If it is not present in the document, assign high severity."
        },
        "Project Assumptions": {
            "search_query": "Retrieve all Project Assumptions-related sections",
            "validation_questions": [
               
                "Are systems, tools, platforms needed for supplier work mentioned? If not, assign high severity",
                "Is information about access to McKesson subject matter experts? If not, assign high severity",
                "Is info about who provides what documentation and in what order given? If not, assign high severity",
                "Is the process for raising and approving change requests (scope, resources, or deliverables change) clearly defined? If not, assign medium severity."  
            ],

            "tag":"This section is mandatory. If it is not present in the document, assign high severity."
        },
        "McKesson Responsibilities": {
            "search_query": "Retrieve the McKesson responsibilities section",
            "validation_questions": [
               
                "Access and Licenses: Does the McKesson responsibilities section specify required access and licenses? If not, assign medium severity.",
                "Support from Analysts/SMEs: Does the McKesson responsibilities section list support from analysts or subject matter experts (SMEs)? If not, assign medium severity."
            ],

            "tag": "This section is optional. If it is not present in the document, assign low severity."
        },
        "Change Control Procedure": {
            "search_query": "Retrieve the change control procedure section",
            "validation_questions": [
                "Change Control Process: Is the process for handling scope, pricing, or timeline changes (via Change Order) clearly described? If not, assign high severity."
                
               
            ],

            "tag":  "This section is mandatory. If it is not present in the document, assign high severity."
        },
        "Financial Information": {
            "search_query": "Retrieve only the Financial Information section and verify that exactly one checkbox is selected for Financial Information",
            "validation_questions": [
                "Check only the checkbox for financial information if EXACTLY ONE checkbox is marked (either Yes or No, but not both or neither). No need to verify if it's logical."
                
                
            ],

        "tag": "This section is mandatory. If it is not present in the document, assign high severity."
        },
        "PII or PHI": {
            "search_query": "Retrieve only the PII or PHI section and verify that exactly one checkbox is selected for PII or PHI",
            "validation_questions": [
                "Check only the checkbox for PII/PHI if EXACTLY ONE checkbox is marked (either Yes or No, but not both or neither). No need to verify if it's logical."
       
            ],

            "tag": "This section is mandatory. If it is not present in the document, assign high severity."
        },
        "Sensitive Information": {
            "search_query": "Retrieve only the Sensitive Information section and verify that exactly one checkbox is selected for sensitive Information",
            "validation_questions": [
                "Check only the checkbox for sensitive information if EXACTLY ONE checkbox is marked (either Yes or No, but not both or neither). No need to verify if it's logical."
              
            ],

             "tag" : "This is section is mandatory. If it is not present in the document, assign high severity."
        },
        "Access": {
            "search_query": "Retrieve only the Access section and verify that exactly one checkbox is selected for Access",
            "validation_questions": [
                "Check only the checkbox for access if checkbox is marked (Yes or No or neither, but not both). No need to verify if it's logical."
              
            ],
            "tag" : "This section is mandatory. If it is not present in the document, assign high severity."
        },
        "Artificial Intelligence": {
            "search_query": "Retrieve only the Artificial Intelligence (AI) section and verify that exactly one checkbox is selected for Artificial Intelligence",
            "validation_questions": [
               
                "Check only the checkbox for artificial intelligence if checkbox is marked (Yes or No or neither, but not both). No need to verify if it's logical.",
                "If this section is not present, assign low severity."
            ],

            "tag":"This section is optional. If it is not present in the document, assign low severity."
        },
        "Exhibits": {
            "search_query": "Retrieve all the chunks related to exhibits to SOW section",
            "validation_questions": [
                "Are all referenced exhibits included and properly numbered?",
                "Do exhibit references match the actual exhibits provided?",
                "Is there consistency in exhibit naming and referencing?"
            ],
            "tag": "This section is optional. If it is not present in the document, assign low severity."
        },
       
        "Statement of Work Characteristic": {
            "search_query": "Retrieve all the chunks related to section Exhibit B-Statement of Work Characteristic",
            "validation_questions": [
                "Check if Role, Rate and Location are provided. If not, assign low severity."
            ],

            "tag":"This section is optional. If it is not present in the document, assign low severity."

            

            
        },
        "McKesson Change Order Template": {
            "search_query": "Retrieve all the chunks related to section Exhibit C-McKesson Change Order Template",
            "validation_questions": [
               
                "Supplier Name: Does it contain a supplier name, and is it consistent with other contract documents? If not, assign low severity.",
                "Client Name: Does it contain a client name, and is it consistent with other contract documents? If not, assign low severity.", 
                "Project Name: Does it contain a project name? If not, assign low severity.",
                "Change Order #: Does it contain a change order number? If not, assign low severity.",
                "Reason for Change Order: Is the reason for change order clearly defined? If not, assign low severity.",
                "McKesson Pre Approvers: Does it contain McKesson Pre Approvers (based on Total Dollar Value of Project including this CO)? If not, assign low severity.",
                "Dollar Amount of Change Order: Is dollar amount of change order clearly stated? If not, assign low severity.",
                "Total Dollar Value of Project including this CO: Is total dollar value of project including this CO clearly stated? If not, assign low severity.",
                "Change Order Submission Date: Does it contain a change order submission date? This should not be before or the same dates as SOW effective date. If not, assign low severity.",
                "Change Order Effective Date: Does it contain a change order effective date? This should not be before or the same date as change order submission date, should not be before or the same dates as SOW effective date. If not, assign low severity.",
                "Change Detail and Impacts: 1) Does it contain a clear Timeline (if any, explain in detail why there is a change in the timeline and what is affecting it)? 2) Scope (if any, explain in detail what scope was added – bullet points): 3) Budget (if any, explain why the budget is impacted and needs to change. If not stated clearly, assign low severity."         
            ],
            "tag": "This section is optional. If it is not present in the document, assign low severity."
        },
        "Additional Terms": {
            "search_query": "Retrieve all the chunks related to Additional Terms",
            "validation_questions": [
                "This section is optional, if section presents, check the following otherwise flag it as low severity: ",
                "Termination Condition: Is the termination condition clearly defined? If not, assign low severity.",
                "Termination of Specific Resource: Is the termination condition for specific resources clearly defined? If not, assign low severity."
            ],

            "tag": "This section is optional. If it is not present, assign low severity."
            
        },
        "Project Oversight": {
            "search_query": "Retrieve all the chunks related to Project Oversight",
            "validation_questions": [
                "Supplier Point of Contact: 1) Is contact person name clearly stated? If not, assign high severity. 2) Is email for this contact clearly stated? If not, assign high severity. 3) Is phone number for this contact clearly stated? If not, assign medium severity. 4) Is address for this contact clearly stated? If not, assign medium severity.",
                "McKesson Point of Contact: 1) Is contact person name clearly stated? If not, assign high severity. 2) Is email for this contact clearly stated? If not, assign high severity. 3) Is phone number for this contact clearly stated? If not, assign medium severity. 4) Is address for this contact clearly stated? If not, assign medium severity."
            ],
            "tag": "This section is mandatory. If it is not present, assign high severity."
        }
    }

    # Fixed-Fee validation configuration
    fixedfee_validation_config = {
        "Header": {
            "search_query": "Retrieve the Header section of the SOW containing Supplier Name, SOW Start Date/Effective Date, Client Name, MSA Start Date",
            "validation_questions": [
                "Supplier Name: Does the header section contain a supplier name, and is it consistent with other contract documents?",
                "SOW Start Date: Does the header section contain a SOW start date? Provide the date if present. Only report issues if dates are missing or if they conflict with other contract documents",
                "Client Name: Does the header section contain a client name, and is it consistent with other contract documents?",
                "MSA Start Date: Does the header section contain a MSA start date? Provide the date if present. If value is provided, SOW start date can be same as MSA date or after the MSA date. Only report issues if dates are missing or if they conflict with other contract documents"
               
            ],
            "tag":" This section is mandatory. If it is not present in the document, assign high severity."
        },
        "SOW Term": {
            "search_query": "Retrieve the SOW Term section containing SOW End Date/SOW Completion Date",
            "validation_questions": [
                "SOW End date: Does the sow term section contain a SOW end date/SOW completion date? Provide the date if present. If value is provided, sow end date should be after the sow start date. Only report issues if dates are missing or they are before the sow start date or if they conflict with other contract documents",
                
            ],
             "tag" : "This section is mandatory. If it is not present in the document, assign high severity."
        },
        "Scope of Services": {
            "search_query": "Retrieve the most relevant chunks for Scope of Services section",
            "validation_questions": [
                "Project Overview: Does the scope of services section contain project overview with clear and precise language? If overview present, does it have a project name?",
                "Project Scope: Does the scope of services section contain project scope with clear and precise language?",
                "Out-of-Scope Work: Does the scope of services section contain out-of-scope work with clear and precise language? If it is not present in the section, assign medium severity",
                "Assumptions: Does the scope of services section contain assumptions which are made in preparing the SOW (that contain dependencies and constraints, risks and mitigation strategies)? If assumptions are not mentioned, assign medium severity", 
                "Resource Name: Does the scope of service section contain resource name (Person name who will be working). If it is not mentioned, assign low severity",
                "Role: Does the scope of service section contain role (role title/skill of the resource e.g. BI Developer (Power BI))? If it is not mentioned, assign high severity",
                "Delivery Location: Does the scope of service section contain delivery location (resource location)? If it is not mentioned, assign medium severity"
                
            ],

            "tag":"This section is mandatory. If it is not present in the document, assign high severity."
        },  
        "Compensation": {
            "search_query": "Retrieve the most relevant chunks for section compensation including deliverables, milestones, compensation and acceptance criteria.",
            "validation_questions": [
                "Is payment terms fixed cost clearly stated with language Total Not to Exceed Fixed Fee basis and the deliverables and milestone section should have milestones/deliverables listed along with the cost of each deliverable and acceptance criteria. If this is not stated clearly, assign high severity",
                "Are deliverables and milestones clearly listed and defined? If not mentioned clearly, assign high severity",
                "Is acceptance criteria - quality standards and metrics, review and testing procedures and quality control measures - clearly stated? If not, assign high severity",
                "Is payment clearly linked to milestones/deliverables? This should include list of deliverable names, detailed description, specification and criteria for completion. If not mentioned clearly, assign high severity",
                "Is the total fixed fee amount clearly listed? If it is not mentioned, assign high severity",     
                "Are invoicing terms - payment schedule, method, invoicing condition, and any exceptions - clearly defined? SOW must have a reference to MSA payment terms called out like payment's terms in the agreement. There should NOT be language like Net60, Net30, payment in 30 days, etc phrases. If it is not clearly stated, assign high severity",
                "Does the break of cost given in exhibit Deliverables, Milestones and Compensation add up to the total given in compensation section? Is the total given in the exhibit match with that total cost given in the compensation section?"
               
            ],
            "tag":"This section is mandatory. If it is not present in the document, assign high severity."
        },
        "Project Assumptions": {
            "search_query": "Retrieve all Project Assumptions-related sections",
            "validation_questions": [
              
                "Are systems, tools, platforms needed for supplier work mentioned? If not, assign high severity",
                "Is information about access to McKesson subject matter experts? If not, assign high severity",
                "Is info about who provides what documentation and in what order given? If not, assign high severity",
                "Is the process for raising and approving change requests (scope, resources, or deliverables change) clearly defined? If not, assign medium severity."  
            ],
            "tag":"This section is mandatory. If it is not present in the document, assign high severity."
        },
        "McKesson Responsibilities": {
            "search_query": "Retrieve the McKesson responsibilities section",
            "validation_questions": [
               
                "Access and Licenses: Does the McKesson responsibilities section specify required access and licenses? If not, assign medium severity.",
                "Support from Analysts/SMEs: Does the McKesson responsibilities section list support from analysts or subject matter experts (SMEs)? If not, assign medium severity."
            ],

            "tag":"This section is optional. If it is not present in the document, assign low severity."
        },
        "Change Control Procedure": {
            "search_query": "Retrieve the change control procedure section",
            "validation_questions": [
                "Change Control Process: Is the process for handling scope, pricing, or timeline changes (via Change Order) clearly described? If not, assign high severity."
                
            ],

            "tag": "This section is mandatory. If it is not present in the document, assign high severity."
        },
        "Financial Information": {
            "search_query": "Retrieve only the Financial Information section and verify that exactly one checkbox is selected for Financial Information",
            "validation_questions": [
                "Check only the checkbox for financial information if EXACTLY ONE checkbox is marked (either Yes or No, but not both or neither). No need to verify if it's logical."
               
            ],
            "tag": "This section is mandatory. If it is not present in the document, assign high severity."
        },
        "PII or PHI": {
            "search_query": "Retrieve only the PII or PHI section and verify that exactly one checkbox is selected for PII or PHI",
            "validation_questions": [
                "Check only the checkbox for PII/PHI if EXACTLY ONE checkbox is marked (either Yes or No, but not both or neither). No need to verify if it's logical."
                "This is section is mandatory. If it is not present in the document, assign high severity."
            ],
            "tag": "This section is mandatory. If it is not present in the document, assign high severity."
        },
        "Sensitive Information": {
            "search_query": "Retrieve only the Sensitive Information section and verify that exactly one checkbox is selected for sensitive Information",
            "validation_questions": [
                "Check only the checkbox for sensitive information if EXACTLY ONE checkbox is marked (either Yes or No, but not both or neither). No need to verify if it's logical."
             
            ],
            "tag": "This section is mandatory. If it is not present in the document, assign high severity."
        },
        "Access": {
            "search_query": "Retrieve only the Access section and verify that exactly one checkbox is selected for Access",
            "validation_questions": [
                "Check only the checkbox for access if checkbox is marked (Yes or No or neither, but not both). No need to verify if it's logical."
              
            ],
            "tag": "This section is mandatory. If it is not present in the document, assign high severity."
        },
        "Artificial Intelligence": {
            "search_query": "Retrieve only the Artificial Intelligence (AI) section and verify that exactly one checkbox is selected for Artificial Intelligence",
            "validation_questions": [
              
                "Check only the checkbox for artificial intelligence if checkbox is marked (Yes or No or neither, but not both). No need to verify if it's logical."
              
            ],
            "tag":   "This section is optional. If this section is not present, assign low severity."
        },
        "Exhibits": {
            "search_query": "Retrieve all the chunks related to exhibits to SOW section",
            "validation_questions": [
                "Are all referenced exhibits included and properly numbered?",
                "Do exhibit references match the actual exhibits provided?",
                "Is there consistency in exhibit naming and referencing?"
            ],
            "tag": "This section is optional. If this section is not present, assign low severity."
        },
        "Deliverables, Milestones and Compensation": {
            "search_query": "Retrieve all the chunks related to exhibit section Exhibit A-Deliverables, Milestones and Compensation",
            "validation_questions": [
                "This section is MANDATORY for Fixed-Fee SOW, if section presents, check the following otherwise flag it with high severity: ",
                "Deliverable No.: Are the deliverable numbers provided? If not, assign low severity.",
                "Deliverable/Milestone: Are the deliverables and milestones clearly defined? If not, assign high severity.",
                "Due Date: Is the due date provided? It doesn't have to be a date, it can be week 4, month 2, any kinds of time reference. If not, assign high severity.",
                "Acceptance Criteria: Is acceptance criteria - quality standards and metrics, review and testing procedures and quality control measures - clearly stated? If not, assign high severity.",
                "Invoice Amount: Is invoice amount clearly defined? If not, assign high severity.",
                "Does the sum of all deliverable amounts match the total project cost mentioned in the Compensation section? If not, assign medium severity."
            ],
            "tag":"This section is MANDATORY for Fixed-Fee SOW. If it is not present in case of Fixed-Fee SOW, assign high severity."
        },
        "Statement of Work Characteristic": {
            "search_query": "Retrieve all the chunks related to section Exhibit B-Statement of Work Characteristic",
            "validation_questions": [
                "This section is optional for Fixed-Fee SOW, if section presents, check if Role and Location are provided (Rate is not mandatory for Fixed-Fee). If role or location not provided, assign low severity. If section is not present, flag it with low severity."
            ],

            "tag":"This section is optional for Fixed-Fee SOW. If not present in the document, assign low severity."
            
            
        },
        "McKesson Change Order Template": {
            "search_query": "Retrieve all the chunks related to section Exhibit C-McKesson Change Order Template",
            "validation_questions": [
               
                "Supplier Name: Does it contain a supplier name, and is it consistent with other contract documents? If not, assign low severity.",
                "Client Name: Does it contain a client name, and is it consistent with other contract documents? If not, assign low severity.", 
                "Project Name: Does it contain a project name? If not, assign low severity.",
                "Change Order #: Does it contain a change order number? If not, assign low severity.",
                "Reason for Change Order: Is the reason for change order clearly defined? If not, assign low severity.",
                "McKesson Pre Approvers: Does it contain McKesson Pre Approvers (based on Total Dollar Value of Project including this CO)? If not, assign low severity.",
                "Dollar Amount of Change Order: Is dollar amount of change order clearly stated? If not, assign low severity.",
                "Total Dollar Value of Project including this CO: Is total dollar value of project including this CO clearly stated? If not, assign low severity.",
                "Change Order Submission Date: Does it contain a change order submission date? This should not be before or the same dates as SOW effective date. If not, assign low severity.",
                "Change Order Effective Date: Does it contain a change order effective date? This should not be before or the same date as change order submission date, should not be before or the same dates as SOW effective date. If not, assign low severity.",
                "Change Detail and Impacts: 1) Does it contain a clear Timeline (if any, explain in detail why there is a change in the timeline and what is affecting it)? 2) Scope (if any, explain in detail what scope was added – bullet points): 3) Budget (if any, explain why the budget is impacted and needs to change. If not stated clearly, assign low severity."         
            ],
            "tag":"This section is optional. If it is not present, assign low severity."
        },
        "Additional Terms": {
            "search_query": "Retrieve all the chunks related to Additional Terms",
            "validation_questions": [
               
                "Termination Condition: Is the termination condition clearly defined? If not, assign low severity.",
                "Termination of Specific Resource: Is the termination condition for specific resources clearly defined? If not, assign low severity."
            ],
            "tag":"This section is optional. If it is not present, assign low severity."
        },
        "Project Oversight": {
            "search_query": "Retrieve all the chunks related to Project Oversight",
            "validation_questions": [
                "Supplier Point of Contact: 1) Is contact person name clearly stated? If not, assign high severity. 2) Is email for this contact clearly stated? If not, assign high severity. 3) Is phone number for this contact clearly stated? If not, assign medium severity. 4) Is address for this contact clearly stated? If not, assign medium severity.",
                "McKesson Point of Contact: 1) Is contact person name clearly stated? If not, assign high severity. 2) Is email for this contact clearly stated? If not, assign high severity. 3) Is phone number for this contact clearly stated? If not, assign medium severity. 4) Is address for this contact clearly stated? If not, assign medium severity."
            ],
            "tag":"This section is mandatory. If it is not present, assign high severity."
        }
    }
    
    # Determine which configuration to return based on SOW type
    if sow_type and ("T&M" in str(sow_type).upper() or "TIME" in str(sow_type).upper() or "MATERIAL" in str(sow_type).upper()):
        return TM_validation_config
    elif sow_type and ("FIXED" in str(sow_type).upper() or "FEE" in str(sow_type).upper()):
        return fixedfee_validation_config
    else:
        # Default to T&M if type cannot be determined
        print(f"Warning: Unknown SOW type '{sow_type}'. Defaulting to T&M validation config.")
        return TM_validation_config

    
def complete(model, prompt):
    return Complete(model, prompt).replace("$", "\$")

def clean_and_parse_json(llm_response):
    """
    Robust JSON parsing with multiple fallback strategies
    """
    try:
        # First try: Direct parsing
        return json.loads(llm_response)
    except json.JSONDecodeError:
        pass
    
    # Clean the response
    cleaned_response = llm_response.strip()
    
    # Remove any markdown formatting
    cleaned_response = re.sub(r'```json\s*', '', cleaned_response)
    cleaned_response = re.sub(r'```\s*$', '', cleaned_response)
    
    # Try to find JSON object
    json_patterns = [
        r'\{[\s\S]*?"sow_validation"[\s\S]*?\}(?=\s*$)',  # Complete JSON with sow_validation
        r'\{[\s\S]*?\}(?=\s*$)',  # Any complete JSON object
        r'\{[\s\S]*?"sow_validation"[\s\S]*?\][\s\S]*?\}',  # JSON with array closure
    ]
    
    for pattern in json_patterns:
        matches = re.findall(pattern, cleaned_response, re.DOTALL)
        for match in matches:
            try:
                # Clean up common JSON issues
                json_str = match.strip()
                # Fix common trailing comma issues
                json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
                # Fix missing quotes around keys
                json_str = re.sub(r'(\w+):', r'"\1":', json_str)
                # Already quoted keys should not be double-quoted
                json_str = re.sub(r'""(\w+)"":', r'"\1":', json_str)
                
                return json.loads(json_str)
            except json.JSONDecodeError:
                continue
    
    # If all fails, return error structure
    return None

def validate_sow_with_llm(sow_chunks, section_name, section_specific_questions, severity_tag):
    """
    Use Cortex Complete to validate SOW content using retrieved document chunks and section-specific questions.
    Returns LLM-generated analysis of inconsistencies, violations, or alignment.
    """
    sow_content = ""
    # Fix: Handle the list of chunks directly since we're not passing 'results' wrapper
    if isinstance(sow_chunks, list):
        for chunk in sow_chunks:
            if 'chunk' in chunk:
                sow_content += chunk['chunk'] + "\n\n"
    elif isinstance(sow_chunks, dict) and 'results' in sow_chunks:
        for result in sow_chunks['results']:
            if 'chunk' in result:
                sow_content += result['chunk'] + "\n\n"

    # If no content found, return empty validation
    if not sow_content.strip():
        severity = "low"
        if "high" in severity_tag.lower():
            severity = "high"
        elif "medium" in severity_tag.lower():
            severity = "medium"
        elif "low" in severity_tag.lower():
            severity = "low"
        return {       "sow_validation": [{
                "section": section_name,
                "issue_number": 1,
                "description": f"Section '{section_name}' is missing from the document",
                "severity": severity,
                "suggested_resolution": f"Add the missing '{section_name}' section to the SOW document"
            }]}



    tag = severity_tag
    # Format section-specific questions for better readability
    formatted_questions = ""
    if isinstance(section_specific_questions, list):
        for i, question in enumerate(section_specific_questions, 1):
            formatted_questions += f"{i}. {question}\n"
    else:
        formatted_questions = section_specific_questions

    #st.markdown(sow_content)
    #st.markdown(formatted_questions)
    # Updated prompt with better instructions
    comparison_prompt = f"""You are an expert contract reviewer tasked with validating a Statement of Work (SOW) for completeness, clarity, consistency, and accuracy.
    You are validating the {section_name} section of a Statement of Work (SOW). You have been provided document content {sow_content}, {formatted_questions} and {tag}
    as validation criteria for validating the section. 

    ### Validate {section_name} as per {formatted_questions}.

  ### If you do not find relevant document content for {section_name}, then assign severity according to the provided {tag} and you should give output in JSON format as below. 
    {{
    "sow_validation": [
        {{
            "section": "{section_name}",
            "description": "Brief specific issue description",
            "severity": "high/medium/low according to the {tag}",
            "suggested_resolution": "Specific action needed",
            "issue_number": 1
        }}
    ]
}}

   




CHECKBOX VALIDATION RULES(if any):
- "Yes☒No☐" or "Yes X No ☐" (Yes checked, No unchecked) = VALID
- "Yes☐No☒" or "Yes ☐ No X" (Yes unchecked, No checked) = VALID  
- "Yes☒No☒" or "Yes X No X"(both checked) = ISSUE
- "Yes☐No☐" or "Yes ☐ No ☐" (neither checked) = ISSUE
- If checkboxes are missing, still Issue

INSTRUCTIONS:
1. Only flag actual issues, not missing optional information
2. For checkbox sections: Only report issues if both boxes are checked or both are empty
3. For compensation: Only flag if amounts are contradictory or completely missing
4. Be objective and focus on clear violations of the criteria
5. If content appears compliant, return empty validation array
6. STRICTLY validate the section based on provided questions and rules, DO NOT MAKE YOUR OWN ASSUMPTIONS/QUESTIONS TO VALIDATE. 

REQUIRED JSON FORMAT (respond with ONLY this JSON, no other text):
{{
    "sow_validation": [
        {{
            "section": "{section_name}",
            "description": "Brief specific issue description",
            "severity": "high/medium/low",
            "suggested_resolution": "Specific action needed",
            "issue_number": 1
        }}
    ]
}}

If no issues found, return: {{"sow_validation": []}}



JSON ONLY - NO OTHER TEXT:"""

    try:
        llm_response = complete("openai-gpt-4.1", comparison_prompt)
        
        # Debug: Show the raw response for troubleshooting (optional, can be removed)
        # st.write(f"**Debug - Raw LLM Response for {section_name}:**")
        # st.text(llm_response[:500] + "..." if len(llm_response) > 500 else llm_response)
        
        # Use improved JSON parsing
        validation_result = clean_and_parse_json(llm_response)
        
        if validation_result is None:
            # Fallback: try to determine if section is compliant from response content
            if any(word in llm_response.lower() for word in ["no issues", "compliant", "valid", "acceptable"]):
                return {"sow_validation": []}
            else:
                return {
                    "sow_validation": [{
                        "section": section_name,
                        "issue_number": 1,
                        "description": f"JSON parsing failed. Raw response indicates potential issues. Response preview: {llm_response[:200]}...",
                        "severity": "medium",
                        "suggested_resolution": "Review the section manually as automated parsing failed"
                    }]
                }
        
        return validation_result
            
    except Exception as e:
        return {
            "sow_validation": [{
                "section": section_name,
                "issue_number": 1,
                "description": f"Error during LLM validation: {str(e)}",
                "severity": "high",
                "suggested_resolution": "Check system configuration and try again"
            }]
        }

def identify_sow_type_with_llm(sow_content):
    """
    Use Cortex Complete to identify SOW type from content.
    Returns LLM-generated identification of SOW type.
    """
    if not sow_content.strip():
        return {"sow_type": "Unable to determine - no content found"}

    identification_prompt = f"""You have been provided document content from a Statement of Work 
    (SOW). 

Document Content:
{sow_content}

Analyze this content and determine if the SOW is Time & Materials (T&M) or Fixed-Fee type based on 
these criteria:

T&M Indicators:
- Language like "Total Not to Exceed Fee basis"
- Hourly rates and total hours specified
- Resource roles with billing rates
- Payment based on time spent

Fixed-Fee Indicators:
- Language like "Total Not to Exceed Fixed Fee basis"
- Deliverables and milestones with specific costs
- Payment tied to deliverable completion
- Acceptance criteria for deliverables

Provide your analysis in the following JSON format ONLY:

{{
    "sow_type": "T&M" or "Fixed-Fee"
   
}}

JSON ONLY - NO OTHER TEXT:"""

    try:
        llm_response = complete("openai-gpt-4.1", identification_prompt)
        
        # Use the existing JSON parsing function
        type_result = clean_and_parse_json(llm_response)
        
        if type_result is None:
            return {
                "sow_type": "Unable to determine - JSON parsing failed",
                "confidence": "Low",
               
                "reasoning": "Failed to parse LLM response"
            }
        
        return type_result
            
    except Exception as e:
        return {
            "sow_type": f"Error during analysis: {str(e)}",
            "confidence": "Low", 
         
            "reasoning": "System error occurred"
        }


    
def get_severity_icon(severity):
    if severity.lower() == "high":
        return "🔴"
    elif severity.lower() == "medium":
        return "🟡"
    elif severity.lower() == "low":
        return "🟢"
    else:
        return "⚪"

def get_severity_label(severity):
    if severity.lower() == "high":
        return "🔴 **High**"
    elif severity.lower() == "medium":
        return "🟡 **Medium**"
    elif severity.lower() == "low":
        return "🟢 **Low**"
    else:
        return "⚪ **Unknown**"

def get_section_issues(validation_output, section_name):
    if not validation_output or "sow_validation" not in validation_output:
        return []
    return [item for item in validation_output["sow_validation"] if item.get("section") == section_name]

def get_section_summary(issues):
    if not issues:
        return 0, None, ""
    issue_count = len(issues)
    severities = [issue.get("severity", "").lower() for issue in issues]
    if "high" in severities:
        highest_severity = "high"
    elif "medium" in severities:
        highest_severity = "medium"
    elif "low" in severities:
        highest_severity = "low"
    else:
        highest_severity = None
    
    # Create summary text
    if issue_count == 1:
        summary_text = "1 Point to review"
    else:
        summary_text = f"{issue_count} Points to review"
    
    return issue_count, highest_severity, summary_text

def get_section_header_with_icon(section, issue_count, highest_severity, summary_text):
    if issue_count > 0:
        if highest_severity == "high":
            icon = "❗"
        else:
            icon = ""
        return f"{section} {icon} ({summary_text})"
    else:
        return f"{section}"

def upload_to_stage(file, stage_name):
    with tempfile.NamedTemporaryFile(delete=False, suffix=file.name) as tmp_file:
        tmp_file.write(file.getvalue())
        tmp_file_path = tmp_file.name

    put_result = session.file.put(
        tmp_file_path,
        stage_name,
        overwrite=True,
        auto_compress=False
    )
    return file.name

# Function to convert DOCX → PDF
def convert_docx_to_pdf(docx_path, pdf_path):
    doc = Document(docx_path)
    pdf = SimpleDocTemplate(pdf_path)
    styles = getSampleStyleSheet()
    elements = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            elements.append(Paragraph(text, styles["Normal"]))
            elements.append(Spacer(1, 12))

    pdf.build(elements)



def reset_sow_session_state():
    """Clear all SOW-related session state keys when a new file is uploaded."""
    keys_to_clear = [
        'cortex_service_created',
        'processing_complete',
        'current_file_name',
        'sow_type',
        'active_validation_config',
        'validation_output',
        'categories',
        'uploaded_sow_filename',
        'section_chunks'   # NEW: store chunks safely
    ]
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]


if 'cortex_service_created' not in st.session_state:
    st.session_state['cortex_service_created'] = False

if 'processing_complete' not in st.session_state:
    st.session_state['processing_complete'] = False

if 'current_file_name' not in st.session_state:
    st.session_state['current_file_name'] = None

if 'section_chunks' not in st.session_state:
    st.session_state['section_chunks'] = {}

# File upload
sow_file = st.file_uploader("Upload your SOW document (PDF or DOCX)", type=["pdf", "docx"], key="sow")

# Check if a new file is uploaded (different from the previous one)
if sow_file is not None:
    current_file_id = f"{sow_file.name}_{sow_file.size}_{hash(sow_file.getvalue())}"
    
    if st.session_state.get('current_file_name') != current_file_id:
        # New file detected - reset all processing states
        #st.session_state['cortex_service_created'] = False
        #st.session_state['processing_complete'] = False

        reset_sow_session_state()
        st.session_state['current_file_name'] = current_file_id


    if st.button("🔄 Re-run validation on this file"):
        reset_sow_session_state()
        st.session_state['current_file_name'] = current_file_id
        
        # Clear previous results
        # if 'sow_type' in st.session_state:
        #     del st.session_state['sow_type']
        # if 'active_validation_config' in st.session_state:
        #     del st.session_state['active_validation_config']
        # if 'validation_output' in st.session_state:
        #     del st.session_state['validation_output']
        # if 'categories' in st.session_state:
        #     del st.session_state['categories']
        # if 'uploaded_sow_filename' in st.session_state:
        #     del st.session_state['uploaded_sow_filename']

if sow_file and not st.session_state.get('processing_complete', False):
    st.success("Uploading SOW document...")

    # Create a temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, sow_file.name)

        # Save uploaded file temporarily
        with open(file_path, "wb") as f:
            f.write(sow_file.getbuffer())

        # If docx -> convert to PDF
        if sow_file.name.endswith(".docx"):
            pdf_path = os.path.join(tmpdir, sow_file.name.replace(".docx", ".pdf"))
            convert_docx_to_pdf(file_path, pdf_path)
            file_path = pdf_path
            st.info(f"Converted DOCX to PDF: {os.path.basename(file_path)}")

    uploaded_sow_filename = upload_to_stage(sow_file, "@SOW_STAGE")
    st.success(f"SOW uploaded as: {uploaded_sow_filename}")
    
    st.session_state["uploaded_sow_filename"] = uploaded_sow_filename

    # Only create cortex search service if not already created
    if not st.session_state.get('cortex_service_created', False):
        with st.spinner("Processing and creating Cortex Search Service..."):
            sow_files = [row["name"] for row in session.sql("LIST @SOW_STAGE").collect()]
            actual_sow_filename = next((f.split('/')[-1] for f in sow_files if uploaded_sow_filename in f), None)

            if actual_sow_filename:
                st.info(f"Found uploaded SOW: {actual_sow_filename}")
               
                result_sow = session.sql(
                    f"CALL CREATE_SOW_VALIDATION_CORTEX_SEARCH_LANG_NEW('{actual_sow_filename}')"
                ).collect()
                st.success(result_sow[0][0])
                st.session_state['cortex_service_created'] = True

            else:
                st.error("Could not find uploaded files in SOW stage.")
                st.write("SOW Stage files:", sow_files)

    # SOW type identification (only if not already done)
    if 'sow_type' not in st.session_state:
        with st.spinner("Identifying SOW type..."):
            try:
                section_mapping = get_available_sections_mapping()
                
                compensation_content = ""
                scope_content = ""
                
                if "Compensation" in section_mapping:
                    db_section_name = section_mapping["Compensation"]
                    compensation_chunks = query_cortex_search_service(
                        "Retrieve compensation payment terms deliverables milestones hourly rates", 
                        db_section_name
                    )
                    if compensation_chunks:
                        for chunk in compensation_chunks:
                            compensation_content += chunk.get('chunk', '') + "\n\n"
                
                if "Scope of Services" in section_mapping:
                    db_section_name = section_mapping["Scope of Services"]
                    scope_chunks = query_cortex_search_service(
                        "Retrieve scope services roles hourly rates deliverables", 
                        db_section_name
                    )
                    if scope_chunks:
                        for chunk in scope_chunks[:3]:
                            scope_content += chunk.get('chunk', '') + "\n\n"
                
                combined_content = f"Compensation Section:\n{compensation_content}\n\nScope of Services Section:\n{scope_content}"

                if combined_content.strip():
                    sow_type_result = identify_sow_type_with_llm(combined_content)
                    sow_type = sow_type_result.get('sow_type', 'Unknown')
                    
                    if 'T&M' in sow_type or 'Time' in sow_type:
                        active_validation_config = get_validation_config_by_sow_type("T&M")
                    elif 'Fixed' in sow_type:
                        active_validation_config = get_validation_config_by_sow_type("Fixed-Fee")
                    else:
                        st.warning(f"❓ **Type:** {sow_type}")
                        st.info("Using Time & Materials validation rules as default")
                        active_validation_config = get_validation_config_by_sow_type("T&M")
                else:
                    st.warning("Could not retrieve content for SOW type identification")

                st.session_state['sow_type'] = sow_type_result
                st.session_state['active_validation_config'] = active_validation_config
                       
            except Exception as e:
                st.error(f"Error during SOW type identification: {str(e)}")
                st.session_state['sow_type'] = {"sow_type": "Unknown"}
                st.session_state['active_validation_config'] = get_validation_config_by_sow_type("T&M")

    # Validation (only if not already done)
    if 'validation_output' not in st.session_state:
        with st.spinner("Running LLM validation checks..."):
            try:
                if 'active_validation_config' in st.session_state:
                    validation_config_to_use = st.session_state['active_validation_config']
                else:
                    validation_config_to_use = get_validation_config_by_sow_type("T&M")
            
                validation_output = {"sow_validation": []}
                section_mapping = get_available_sections_mapping()
                categories = list(validation_config_to_use.keys())
                section_chunks_dict = {}

                for section_name, config in validation_config_to_use.items():
                    if section_name in section_mapping:
                        db_section_name = section_mapping[section_name]
                        st.info(f"Validating {section_name} section (DB: {db_section_name})...")
                        
                        sow_chunks = query_cortex_search_service(config["search_query"], db_section_name)

                        
                        section_chunks_dict[section_name] = sow_chunks if sow_chunks else []
                        # if sow_chunks:
                        #     st.success(f"Found {len(sow_chunks)} chunks for {section_name}")
                        #     for i, chunk in enumerate(sow_chunks):
                        #       with st.expander(f"Chunk {i+1} for {section_name}"):
                        #         st.write(chunk.get('chunk', 'No content'))
                        # else:
                        #     st.warning(f"No chunks found for {section_name} section")
                            
                        result = validate_sow_with_llm(
                                sow_chunks, 
                                section_name, 
                                config["validation_questions"], 
                                config["tag"]
                            )
                            
                        if result and "sow_validation" in result:
                                for issue in result["sow_validation"]:
                                    if issue.get("section") == section_name:
                                        validation_output["sow_validation"].append(issue)
                      
                    else:
                        st.warning(f"Section '{section_name}' not found in document")
                        severity_tag = config.get("tag", "")
                        severity = "low"
                        if "high" in severity_tag.lower():
                            severity = "high"
                        elif "medium" in severity_tag.lower():
                            severity = "medium"
                        elif "low" in severity_tag.lower():
                            severity = "low"
            
                        missing_section_issue = {
                            "section": section_name,
                            "issue_number": 1,
                            "description": f"Section '{section_name}' is completely missing from the document",
                            "severity": severity,
                            "suggested_resolution": f"Add the missing '{section_name}' section to the SOW document with all required information"
                        }
                        section_chunks_dict[section_name] = []
                        validation_output["sow_validation"].append(missing_section_issue)

                st.session_state['section_chunks'] = section_chunks_dict
                st.session_state['validation_output'] = validation_output
                st.session_state['categories'] = categories
                st.session_state['processing_complete'] = True

            except Exception as e:
                st.error(f"Error during validation: {str(e)}")
                import traceback
                st.error(traceback.format_exc())
                validation_output = {"sow_validation": []}

# Display results (only show if processing is complete)
#if st.session_state.get('processing_complete', False):
validation_output = st.session_state.get('validation_output', {"sow_validation": []})
categories = st.session_state.get('categories', [])
sow_type_result = st.session_state.get('sow_type', {"sow_type": "Unknown"})

st.markdown("---")
st.subheader("SOW Type Identified")
with st.expander("**SOW Type**", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            sow_type = sow_type_result.get('sow_type', 'Unknown')
            if 'T&M' in sow_type or 'Time' in sow_type:
                st.success(f"Type: {sow_type}")
            elif 'Fixed' in sow_type:
                st.success(f"Type: {sow_type}")
            else: 
                st.info(f"SOW Type is neither T&M nor Fixed-fee")
                       
        
if validation_output:
        st.markdown("---")
        st.subheader("SOW Clause Analysis")

        if "sow_validation" in validation_output:
            total_issues = len(validation_output["sow_validation"])
            high_issues = sum(1 for item in validation_output["sow_validation"] if item.get("severity", "").lower() == "high")
            medium_issues = sum(1 for item in validation_output["sow_validation"] if item.get("severity", "").lower() == "medium")
            low_issues = sum(1 for item in validation_output["sow_validation"] if item.get("severity", "").lower() == "low")
            
            col1, col2, col3, col4 = st.columns(4)
            with col1: st.metric("Total Issues", total_issues)
            with col2: st.metric("🔴 High", high_issues)
            with col3: st.metric("🟡 Medium", medium_issues)
            with col4: st.metric("🟢 Low", low_issues)

        st.markdown("---")
        
        # Toggle button for high-priority issues - this will NOT trigger reprocessing
        col1, col2 = st.columns(2)
        with col1:
          show_high_only = st.toggle("Show only high-priority sections", value=False, 
                                   help="Toggle to view only sections with high-priority issues")

        #with col2:
            #show_debug_chunks = st.toggle("Show chunks for debugging", value=False, 
                                          #help="Toggle to show/hide retrieved chunks for debugging")

        if show_high_only:
            high_priority_sections = []
            for section in categories:
                section_issues = get_section_issues(validation_output, section)
                if any(issue.get("severity", "").lower() == "high" for issue in section_issues):
                    high_priority_sections.append(section)
            
            sections_to_display = high_priority_sections
            
            if not sections_to_display:
                st.info("No high-priority issues found in any section!")
            else:
                st.info(f"Showing {len(sections_to_display)} section(s) with high-priority issues")
        else:
            sections_to_display = categories
            st.info(f"Showing all {len(sections_to_display)} sections")

        # if show_debug_chunks and sections_to_display:
        #     st.markdown("---")
        #     st.subheader("Debug: Retrieved Document Chunks")
        #     st.caption("This section shows the actual text chunks retrieved from the document for analysis")
            
        #     section_chunks_all = st.session_state.get('section_chunks', {})
            
        #     for section in sections_to_display:
        #         section_chunks = section_chunks_all.get(section, [])
        #         if section_chunks:
        #             with st.expander(f" {section} Section - {len(section_chunks)} chunks retrieved", expanded=False):
        #                 for i, chunk in enumerate(section_chunks, 1):
        #                     st.markdown(f"**Chunk {i}:**")
        #                     st.text_area(f"chunk_{section}_{i}", 
        #                                value=chunk.get('chunk', 'No content'), 
        #                                height=150, 
        #                                key=f"debug_chunk_{section}_{i}",
        #                                disabled=True)
        #                     if i < len(section_chunks):
        #                         st.markdown("---")
        #         else:
        #             st.info(f" {section} Section: No chunks retrieved")
            
        #     st.markdown("---")



        for section in sections_to_display:
            section_issues = get_section_issues(validation_output, section)
            issue_count, highest_severity, summary_text = get_section_summary(section_issues)
            accordion_header = get_section_header_with_icon(section, issue_count, highest_severity, summary_text)

            with st.expander(accordion_header):

               



                
                if section_issues:
                    for i, issue in enumerate(section_issues, 1):
                        severity_icon = get_severity_icon(issue.get('severity', 'unknown'))
                        st.markdown(
                            f"<p style='font-size: 12px;'>{severity_icon} <strong>Issue {i}:</strong> {issue.get('description', 'No description')}</p>",
                            unsafe_allow_html=True
                        )
                    
                    resolutions = []
                    for issue in section_issues:
                        resolution = issue.get('suggested_resolution', 'No resolution provided')
                        if resolution and resolution not in resolutions:
                            resolutions.append(resolution)
                    
                    combined_resolution = " ".join(resolutions)
                    st.markdown(f"<p style='font-size: 12px;'><strong>Resolution:</strong> {combined_resolution}</p>", unsafe_allow_html=True)
                    if section == "Compensation":
                        st.markdown(f"<p style='font-size: 12px;'>💡 Insights available to review at the bottom of page</p>", unsafe_allow_html=True)
                else:
                    st.info("✅ No issues found in this section.")

        if validation_output and "sow_validation" in validation_output:
            st.markdown("---")
            st.subheader("All Issues Summary")
            
            issues_data = []
            for item in validation_output["sow_validation"]:
                issues_data.append({
                    "Section": item.get("section", "Unknown"),
                    "Severity": item.get("severity", "Unknown").title(),
                    "Description": item.get("description", "No description"),
                    "Resolution": item.get("suggested_resolution", "No resolution provided")
                })
            
            if issues_data:
                st.dataframe(issues_data, use_container_width=True)
            else:
                st.success("No issues found in the SOW!")

        st.markdown("---")