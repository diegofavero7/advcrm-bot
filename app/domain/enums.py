"""Enums fechados do domínio de triagem jurídica."""

from enum import StrEnum


class LegalArea(StrEnum):
    CIVIL = "civil"
    FAMILY = "family"
    LABOR = "labor"
    CRIMINAL = "criminal"
    CONSUMER = "consumer"
    SOCIAL_SECURITY = "social_security"
    TAX = "tax"
    BUSINESS = "business"
    REAL_ESTATE = "real_estate"
    ADMINISTRATIVE = "administrative"
    TRAFFIC = "traffic"
    SUCCESSION = "succession"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class Intent(StrEnum):
    NEW_LEGAL_LEAD = "new_legal_lead"
    EXISTING_CLIENT_CASE_STATUS = "existing_client_case_status"
    EXISTING_CLIENT_OTHER_REQUEST = "existing_client_other_request"
    NON_LEGAL_CONTACT = "non_legal_contact"
    SPAM = "spam"
    UNDETERMINED = "undetermined"


class Language(StrEnum):
    PT_BR = "pt-BR"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class MessageRole(StrEnum):
    LEAD = "lead"
    AGENT = "agent"
    SYSTEM = "system"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class ContentType(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    DOCUMENT = "document"
    UNSUPPORTED = "unsupported"


class RequestSource(StrEnum):
    WHATSAPP = "whatsapp"
    CRM_MANUAL = "crm_manual"
    SYSTEM = "system"
    IMPORT = "import"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class ParticipantRole(StrEnum):
    CONTACT_PERSON = "contact_person"
    POTENTIAL_CLAIMANT = "potential_claimant"
    AFFECTED_PERSON = "affected_person"
    OPPOSING_PARTY = "opposing_party"
    DEPENDENT = "dependent"
    WITNESS = "witness"
    UNKNOWN = "unknown"


class FactCertainty(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    UNCERTAIN = "uncertain"


class DocumentAvailability(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class UrgencyLevel(StrEnum):
    NORMAL = "normal"
    HIGH = "high"
    IMMEDIATE = "immediate"


class RiskFlag(StrEnum):
    VIOLENCE_OR_THREAT = "violence_or_threat"
    ARREST_OR_DETENTION = "arrest_or_detention"
    DOMESTIC_VIOLENCE = "domestic_violence"
    CHILD_OR_VULNERABLE_PERSON = "child_or_vulnerable_person"
    IMMINENT_DEADLINE = "imminent_deadline"
    LOSS_OF_HOME = "loss_of_home"
    MEDICAL_EMERGENCY = "medical_emergency"
    SELF_HARM = "self_harm"
    SENSITIVE_PERSONAL_DATA = "sensitive_personal_data"
    FRAUD_OR_SCAM = "fraud_or_scam"
    OTHER = "other"


class TriageAction(StrEnum):
    WAIT_FOR_MORE_MESSAGES = "wait_for_more_messages"
    ASK_QUESTION = "ask_question"
    ROUTE_LEAD = "route_lead"
    REQUEST_HUMAN_REVIEW = "request_human_review"
    HUMAN_HANDOFF = "human_handoff"
    IGNORE = "ignore"


class HandoffReason(StrEnum):
    EXISTING_CLIENT = "existing_client"
    CASE_STATUS_REQUEST = "case_status_request"
    IMMEDIATE_RISK = "immediate_risk"
    CRIMINAL_EMERGENCY = "criminal_emergency"
    LEGAL_DEADLINE_RISK = "legal_deadline_risk"
    LOW_CONFIDENCE = "low_confidence"
    CONFLICTING_INFORMATION = "conflicting_information"
    USER_REQUESTED_HUMAN = "user_requested_human"
    UNSUPPORTED_CONTENT = "unsupported_content"
    UNSUPPORTED_REQUEST = "unsupported_request"
    SENSITIVE_SITUATION = "sensitive_situation"
    MAXIMUM_QUESTIONS_REACHED = "maximum_questions_reached"
    SYSTEM_POLICY = "system_policy"
    OTHER = "other"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class TriageState(StrEnum):
    COLLECTING_MESSAGES = "collecting_messages"
    PENDING_CLASSIFICATION = "pending_classification"
    CLASSIFIED = "classified"
    COLLECTING_INFORMATION = "collecting_information"
    WAITING_LEAD_REPLY = "waiting_lead_reply"
    PENDING_ACTION_APPROVAL = "pending_action_approval"
    PENDING_HUMAN_REVIEW = "pending_human_review"
    READY_FOR_HANDOFF = "ready_for_handoff"
    HUMAN_ASSIGNED = "human_assigned"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


# --- Subjects por área (enums fechados) ---


class SocialSecuritySubject(StrEnum):
    RETIREMENT = "retirement"
    PERMANENT_DISABILITY_BENEFIT = "permanent_disability_benefit"
    TEMPORARY_DISABILITY_BENEFIT = "temporary_disability_benefit"
    BPC_LOAS = "bpc_loas"
    PRISON_ALLOWANCE = "prison_allowance"
    PRISON_ALLOWANCE_DENIAL = "prison_allowance_denial"
    DEATH_PENSION = "death_pension"
    MATERNITY_BENEFIT = "maternity_benefit"
    ACCIDENT_BENEFIT = "accident_benefit"
    BENEFIT_DENIAL = "benefit_denial"
    BENEFIT_SUSPENSION = "benefit_suspension"
    BENEFIT_REVIEW = "benefit_review"
    RURAL_BENEFIT = "rural_benefit"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class ConsumerSubject(StrEnum):
    VEHICLE_PURCHASE_IRREGULARITIES = "vehicle_purchase_irregularities"
    VEHICLE_HIDDEN_DEFECT = "vehicle_hidden_defect"
    UNDISCLOSED_AUCTION_HISTORY = "undisclosed_auction_history"
    FINANCING_AMOUNT_DISCREPANCY = "financing_amount_discrepancy"
    UNAUTHORIZED_ADDON = "unauthorized_addon"
    BANKING_FRAUD = "banking_fraud"
    UNAUTHORIZED_CHARGE = "unauthorized_charge"
    WRONGFUL_CREDIT_LISTING = "wrongful_credit_listing"
    PRODUCT_DEFECT = "product_defect"
    SERVICE_FAILURE = "service_failure"
    HEALTH_PLAN = "health_plan"
    ONLINE_PURCHASE = "online_purchase"
    AIR_TRAVEL = "air_travel"
    UTILITY_SERVICE = "utility_service"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class LaborSubject(StrEnum):
    DISMISSAL_WITHOUT_JUST_CAUSE = "dismissal_without_just_cause"
    UNPAID_SEVERANCE = "unpaid_severance"
    OVERTIME = "overtime"
    WAGE_CLAIM = "wage_claim"
    WORKPLACE_ACCIDENT = "workplace_accident"
    HARASSMENT = "harassment"
    DISCRIMINATION = "discrimination"
    FGTS = "fgts"
    OCCUPATIONAL_DISEASE = "occupational_disease"
    EMPLOYMENT_RECOGNITION = "employment_recognition"
    WORKING_HOURS = "working_hours"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class FamilySubject(StrEnum):
    CHILD_CUSTODY = "child_custody"
    CHILD_SUPPORT = "child_support"
    SPOUSAL_SUPPORT = "spousal_support"
    DIVORCE = "divorce"
    DOMESTIC_VIOLENCE = "domestic_violence"
    PATERNITY = "paternity"
    VISITATION = "visitation"
    PROPERTY_DIVISION = "property_division"
    RESTRAINING_ORDER = "restraining_order"
    ADOPTION = "adoption"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class CivilSubject(StrEnum):
    DEBT_COLLECTION = "debt_collection"
    CONTRACT_BREACH = "contract_breach"
    PROPERTY_DAMAGE = "property_damage"
    INDEMNIFICATION = "indemnification"
    NEIGHBOR_DISPUTE = "neighbor_dispute"
    POSSESSION = "possession"
    EXTRA_CONTRACTUAL_LIABILITY = "extra_contractual_liability"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class CriminalSubject(StrEnum):
    FLAGRANT_ARREST = "flagrant_arrest"
    POLICE_INQUIRY = "police_inquiry"
    CRIMINAL_DEFENSE = "criminal_defense"
    VICTIM_COMPLAINT = "victim_complaint"
    PROTECTIVE_MEASURE = "protective_measure"
    DRUG_OFFENSE = "drug_offense"
    THEFT_OR_ROBBERY = "theft_or_robbery"
    DOMESTIC_VIOLENCE_CRIMINAL = "domestic_violence_criminal"
    HABEAS_CORPUS = "habeas_corpus"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class TaxSubject(StrEnum):
    TAX_ASSESSMENT = "tax_assessment"
    TAX_COLLECTION = "tax_collection"
    TAX_REFUND = "tax_refund"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class BusinessSubject(StrEnum):
    COMPANY_FORMATION = "company_formation"
    PARTNER_DISPUTE = "partner_dispute"
    COMMERCIAL_CONTRACT = "commercial_contract"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class RealEstateSubject(StrEnum):
    PURCHASE_SALE = "purchase_sale"
    LEASE = "lease"
    CONSTRUCTION_DEFECT = "construction_defect"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class AdministrativeSubject(StrEnum):
    PUBLIC_SERVICE = "public_service"
    ADMINISTRATIVE_APPEAL = "administrative_appeal"
    LICENSING = "licensing"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class TrafficSubject(StrEnum):
    TRAFFIC_FINE = "traffic_fine"
    LICENSE_SUSPENSION = "license_suspension"
    TRAFFIC_ACCIDENT = "traffic_accident"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class SuccessionSubject(StrEnum):
    INVENTORY = "inventory"
    WILL = "will"
    HEIRSHIP = "heirship"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class GenericSubject(StrEnum):
    OTHER = "other"
    UNDETERMINED = "undetermined"


class PrisonAllowanceSubsubject(StrEnum):
    SPOUSE_RELATIONSHIP = "spouse_relationship"
    PARTNER_RELATIONSHIP = "partner_relationship"
    CHILD_DEPENDENT = "child_dependent"
    PARENT_DEPENDENT = "parent_dependent"
    PRISON_DATE = "prison_date"
    PRISON_REGIME = "prison_regime"
    URBAN_ACTIVITY = "urban_activity"
    RURAL_ACTIVITY = "rural_activity"
    CONTRIBUTION_HISTORY = "contribution_history"
    INCOME_DURING_DETENTION = "income_during_detention"
    PRIOR_REQUEST = "prior_request"
    PRIOR_DENIAL = "prior_denial"
    OTHER = "other"
    UNDETERMINED = "undetermined"


class VehiclePurchaseSubsubject(StrEnum):
    PROFESSIONAL_SELLER = "professional_seller"
    PRIVATE_SELLER = "private_seller"
    PURCHASE_DATE = "purchase_date"
    CONTRACT_VALUES = "contract_values"
    FINANCING_INSTITUTION = "financing_institution"
    HIDDEN_DEFECT = "hidden_defect"
    AUCTION_HISTORY = "auction_history"
    DISCOVERY_CIRCUMSTANCES = "discovery_circumstances"
    PRIOR_RESOLUTION_ATTEMPT = "prior_resolution_attempt"
    REPOSSESSION_RISK = "repossession_risk"
    COLLECTION_RISK = "collection_risk"
    OTHER = "other"
    UNDETERMINED = "undetermined"


PRIORITY_AREAS: frozenset[LegalArea] = frozenset(
    {
        LegalArea.SOCIAL_SECURITY,
        LegalArea.CONSUMER,
        LegalArea.LABOR,
        LegalArea.FAMILY,
        LegalArea.CIVIL,
        LegalArea.CRIMINAL,
    }
)

NON_PRIORITY_AREAS: frozenset[LegalArea] = frozenset(
    {
        LegalArea.TAX,
        LegalArea.BUSINESS,
        LegalArea.REAL_ESTATE,
        LegalArea.ADMINISTRATIVE,
        LegalArea.TRAFFIC,
        LegalArea.SUCCESSION,
        LegalArea.OTHER,
        LegalArea.UNDETERMINED,
    }
)
