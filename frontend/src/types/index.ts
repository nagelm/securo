export interface User {
  id: string
  email: string
  is_active: boolean
  is_superuser: boolean
  is_verified: boolean
  is_2fa_enabled: boolean
  preferences: UserPreferences
}

export interface AdminUser {
  id: string
  email: string
  is_active: boolean
  is_superuser: boolean
  is_verified: boolean
  preferences: UserPreferences | null
}

export interface AdminUserList {
  items: AdminUser[]
  total: number
}

export interface Passkey {
  id: string
  name: string
  transports: string[] | null
  aaguid: string | null
  device_type: string | null
  backed_up: boolean | null
  created_at: string
  last_used_at: string | null
}

export interface PasskeyOptionsResponse {
  challenge_id: string
  options: Record<string, unknown>
}

export interface AppSetting {
  key: string
  value: string
}

export type WorkspaceRole = 'owner' | 'editor' | 'viewer' | 'manager'

export type WorkspaceKind = 'personal' | 'business'

export interface Workspace {
  id: string
  name: string
  // Widened on purpose: a workspace stored before the current kind list
  // still has to render. Writes are narrowed to WorkspaceKind.
  kind: string
  is_archived: boolean
  default_currency: string
  locale: string | null
  /** The calendar this workspace keeps its books in, or null to follow the
   *  application timezone. */
  timezone: string | null
  /** Where the workspace files. Selects the fiscal document pack; never the
   *  interface language. */
  tax_jurisdiction: string | null
  icon: string | null
  color: string | null
  created_at: string
  created_by_user_id: string | null
  managed_by_user_id: string | null
  role: WorkspaceRole | null
  /** Modules this workspace shows. Resolved server-side; see lib/modules.ts. */
  enabled_modules: string[]
}

export interface WorkspaceMember {
  id: string
  user_id: string
  email: string
  display_name: string | null
  role: WorkspaceRole
  joined_at: string
}

export interface UserPreferences {
  language?: string
  date_format?: string
  currency_display?: string
  display_name?: string
  onboarding_completed?: boolean
}

export interface Category {
  id: string
  user_id: string
  group_id: string | null
  name: string
  icon: string
  color: string
  is_system: boolean
  is_hidden: boolean
  treat_as_transfer: boolean
  is_ignored: boolean
}

/** Active rules that assign a category, used when retiring one. */
export interface CategoryRuleUsage {
  rules: { id: string; name: string }[]
}

/** Everything that still points at a category, used when deleting one. */
export interface CategoryUsage {
  transactions: number
  budgets: number
  recurring_transactions: number
  rules: { id: string; name: string }[]
}

export interface CategoryGroup {
  id: string
  user_id: string
  name: string
  icon: string
  color: string
  position: number
  is_system: boolean
  is_hidden: boolean
  categories: Category[]
}

export interface ConnectionInstitution {
  name: string
  logo_url: string | null
}

export interface BankConnection {
  id: string
  user_id: string
  provider: string
  institution_name: string
  display_name: string | null
  logo_url: string | null
  external_id: string
  status: string
  settings: ConnectionSettings | null
  last_sync_at: string | null
  created_at: string
  // Institutions this link spans (issue #345). Empty for one-institution
  // providers — institution_name covers those.
  institutions: ConnectionInstitution[]
}

export interface ConnectionSettings {
  payee_source?: 'auto' | 'merchant' | 'payment_data' | 'description' | 'none'
  import_pending?: boolean
  sync_assets?: boolean
}

/** Enough of an account to draw it in a transfer pill (may be closed). */
export interface TransferAccountRef {
  id: string
  name: string
  display_name: string | null
  type: string
  institution_logo_url: string | null
  is_closed: boolean
}

export interface Account {
  id: string
  user_id: string
  connection_id: string | null
  external_id: string | null
  name: string
  display_name: string | null
  // Place in the user's account order, smallest first (set on the Accounts
  // page). The API lists accounts in this order. Display only.
  position: number
  // Last 4 chars of the bank's identifier for the account, when the provider
  // exposes one. Tells apart accounts a bank reports under an identical name.
  masked_number: string | null
  // Denormalized bank identity from the linked connection (null for manual
  // accounts). Used to render the institution logo next to the account.
  institution_name: string | null
  institution_logo_url: string | null
  type: string
  balance: number
  current_balance: number
  previous_balance: number | null
  balance_primary: number | null
  currency: string
  credit_limit: number | null
  available_credit: number | null
  statement_close_day: number | null
  payment_due_day: number | null
  next_close_date: string | null
  next_due_date: string | null
  minimum_payment: number | null
  card_brand: string | null
  card_level: string | null
  shared_balance_group: string | null
  // Loan accounts only (type === 'loan'); null on every other type.
  loan_subtype: LoanSubtype | null
  secured_asset_id: string | null
  // Loan facts (rate, term, maturity, repayment) in Akahu's shape, typed in
  // by the user or merged in by sync (lib/loan-details.ts). Display only.
  loan_details: Record<string, unknown> | null
  is_closed: boolean
  closed_at: string | null
}

// The kinds of loan a `loan` account may be (backend account_service.LOAN_SUBTYPES).
export type LoanSubtype = 'mortgage' | 'vehicle' | 'student' | 'personal' | 'line_of_credit' | 'other'

export interface CreditCardBill {
  id: string
  account_id: string
  external_id: string
  due_date: string // YYYY-MM-DD
  total_amount: number
  currency: string
  minimum_payment: number | null
}

export interface Collection {
  id: string
  user_id: string
  name: string
  icon: string
  color: string
  position: number
  account_ids: string[]
  account_count: number
  wallet_ids: string[]
  wallet_count: number
}

export interface AccountSummary {
  account_id: string
  current_balance: number
  opening_balance: number
  monthly_income: number
  monthly_expenses: number
  current_balance_primary: number | null
  opening_balance_primary: number | null
  monthly_income_primary: number | null
  monthly_expenses_primary: number | null
  projected_income?: number
  projected_expenses?: number
  projected_income_primary?: number | null
  projected_expenses_primary?: number | null
}

/** Set when this transaction settles an invoice. Absent in workspaces
 *  without the invoicing module — the query behind it does not run there. */
export interface TransactionInvoiceLink {
  invoice_id: string
  number: number | null
  series: string | null
  /** The name an imported invoice arrived with; it carries no number of
   *  ours, so the badge reads this instead. */
  external_number: string | null
  amount: string
}

export interface Transaction {
  /** Every invoice this transaction settles — a payout net of fees
   *  settles several. Absent in a workspace without the module. */
  invoice_links?: TransactionInvoiceLink[] | null
  id: string
  user_id: string
  account_id: string | null
  category_id: string | null
  category: Category | null
  external_id: string | null
  description: string
  original_description: string | null
  amount: number
  currency: string
  date: string
  type: 'debit' | 'credit'
  source: string
  status: 'posted' | 'pending'
  payee: string | null
  payee_id: string | null
  payee_name: string | null
  /** The payee's logo (provider's, else its website favicon), or null. */
  payee_logo_url?: string | null
  /** The icon the user picked for the payee; wins over the logo. */
  payee_icon?: string | null
  notes: string | null
  transfer_pair_id: string | null
  /** The account the other leg of a linked transfer sits in. */
  transfer_counterpart_account_id?: string | null
  /** Drawable summaries of both transfer accounts, closed ones included. */
  transfer_account?: TransferAccountRef | null
  transfer_counterpart_account?: TransferAccountRef | null
  amount_primary: number | null
  fx_rate_used: number | null
  fx_fallback: boolean
  attachment_count?: number
  installment_number: number | null
  total_installments: number | null
  installment_total_amount: number | null
  installment_purchase_date: string | null
  installment_series_id: string | null
  bill_id: string | null
  // Manual override for which credit-card bill cycle this tx belongs to
  // (issue #92). Empty / null = use auto bucketing (Pluggy bill_id when
  // available, cycle math otherwise). Setting it forces the tx into the
  // bill whose due_date matches.
  effective_bill_date: string | null
  // The recurring bill this transaction fulfills, if any (issue #116).
  recurring_transaction_id?: string | null
  splits: TransactionSplit[]
  // Shared-transaction view fields. Set per-request when the viewer
  // is a linked split member but not the owner. Render `viewer_share`
  // as the amount and treat the row as read-only — editing belongs
  // to the parent's owner.
  is_shared?: boolean
  viewer_share?: number | null
  group_id?: string | null
  // Display name of the parent's owner (the person who actually paid).
  // Derived per-request from the group's `is_self` member.
  parent_owner_name?: string | null
  // Flag to exclude this transaction from reports and dashboard aggregations
  is_ignored: boolean
  // Keeps the transaction in the ledger/balance while excluding it from P&L.
  exclude_from_pnl?: boolean
  virtual?: boolean
}

// Scope for installment-series edits/deletes: "this" (default) only touches
// the target row, "future" touches it plus later installments, "all" touches
// the whole series. Ignored server-side for non-installment transactions.
export type TransactionApplyScope = 'this' | 'future' | 'all'

// Payload for POST /api/transactions/installments. `base` is
// the amount repeated as-is; the backend fans it out into `installments`
// equal parcels sharing the installment fingerprint and stores
// installment_total_amount = base.amount * installments.
export interface InstallmentSeriesInput {
  base: {
    account_id: string
    category_id?: string | null
    payee_id?: string | null
    description: string
    amount: number
    date: string
    type: 'debit' | 'credit'
    currency?: string
    notes?: string | null
    status?: 'posted' | 'pending'
    amount_primary?: number | null
    fx_rate_used?: number | null
    effective_bill_date?: string | null
    splits?: TransactionSplitsInput | null
  }
  installments: number
  first_installment_status?: 'posted' | 'pending'
  frequency?: 'monthly' | 'quarterly' | 'semiannual' | 'weekly' | 'biweekly' | 'yearly'
}

export type ShareType = 'equal' | 'exact' | 'percent'

export interface TransactionSplit {
  id: string
  transaction_id: string
  group_member_id: string
  share_amount: number
  share_type: string
  share_pct: number | null
  notes: string | null
  created_at: string
}

export interface TransactionSplitInput {
  group_member_id: string
  share_amount?: number | null
  share_pct?: number | null
  notes?: string | null
}

export interface TransactionSplitsInput {
  share_type: ShareType
  splits: TransactionSplitInput[]
}

// Payload the transaction dialog sends on save. `splits` is the normalized
// TransactionSplitsInput the split section produces, not the
// TransactionSplit[] rows the API returns, so the edit payload type reflects
// the form's actual shape.
export type TransactionEditPayload = Omit<Partial<Transaction>, 'splits'> & {
  splits?: TransactionSplitsInput | null
}

export type GroupKind = 'social' | 'cost_center' | 'project' | 'client' | 'other'

export interface Group {
  id: string
  user_id: string
  name: string
  kind: GroupKind
  default_currency: string
  icon: string
  color: string
  is_archived: boolean
  // Derived server-side per request. False = the current user is a
  // linked member, not the owner — UI should hide edit affordances.
  is_owner: boolean
  notes: string | null
  created_at: string
  members: GroupMember[]
}

export interface GroupMember {
  id: string
  group_id: string
  name: string
  linked_user_id: string | null
  email: string | null
  is_self: boolean
  created_at: string
}

export interface GroupSettlement {
  id: string
  group_id: string
  from_member_id: string
  to_member_id: string
  amount: number
  currency: string
  date: string
  transaction_id: string | null
  notes: string | null
  created_at: string
}

export interface GroupBalanceLine {
  member_id: string
  currency: string
  // Positive = member owes the owner. Negative = owner owes member.
  amount: number
  // FX-converted to the group's default currency for cross-currency rollups.
  amount_in_default_currency: number
}

export interface GroupBalances {
  group_id: string
  self_member_id: string | null
  default_currency: string
  lines: GroupBalanceLine[]
}

/** A fiscal document belonging to a payee. `kind` mirrors the backend's
 *  closed TaxIdKind; the value arrives normalised. */
export interface PayeeTaxId {
  kind: string
  value: string
}

export interface Payee {
  id: string
  user_id: string
  name: string
  /** Legal nature, or null when unknown — the normal state for a row sync created. */
  type: 'person' | 'company' | null
  /** Where the row came from. Server-set at creation and never editable. */
  source: 'manual' | 'sync' | 'import'
  is_favorite: boolean
  notes: string | null
  email: string | null
  phone: string | null
  address: string | null
  website: string | null
  /** The logo image: one the user set, else the merchant logo a provider supplied. */
  logo_url: string | null
  /** An icon the user picked (category icon set); wins over any logo. */
  icon: string | null
  /** What the payee shows: `logo_url`, else a favicon of `website`, else null. */
  display_logo_url: string | null
  tax_ids: PayeeTaxId[]
  created_at: string
  transaction_count: number
}

/** One document kind as the active workspace's jurisdiction describes it.
 *  `offered` marks the ones its pack asks for; the rest stay selectable,
 *  because a counterparty's country is not the workspace's. */
export interface TaxIdKindOption {
  kind: string
  label_key: string
  mask: string | null
  offered: boolean
}

export interface PayeeSummary {
  payee: Payee
  total_spent: number
  total_received: number
  transaction_count: number
  most_common_category: Category | null
  last_transaction_date: string | null
}

export interface RuleCondition {
  field: string
  op: string
  value: string | number
}

/** A nested group of conditions joined by its own operator.
 *
 * Groups let a rule mix AND and OR — `type is debit AND (contains UBER OR
 * contains 99POP)`. They hold leaf conditions only, capping rule depth at the
 * two levels the engine evaluates and the editor exposes.
 */
export interface RuleConditionGroup {
  op: 'and' | 'or'
  conditions: RuleCondition[]
}

/** An entry of a rule's condition list: a leaf condition or one group. */
export type RuleConditionNode = RuleCondition | RuleConditionGroup

export interface RuleAction {
  op: string
  value: string
}

export interface Rule {
  id: string
  user_id: string
  name: string
  conditions_op: 'and' | 'or'
  conditions: RuleConditionNode[]
  actions: RuleAction[]
  priority: number
  is_active: boolean
  apply_to_existing?: boolean
  overwrite_existing_categories?: boolean
}

export interface RuleExportItem {
  name: string
  conditions_op: 'and' | 'or'
  conditions: RuleConditionNode[]
  actions: RuleAction[]
  priority: number
  is_active: boolean
}

export interface RuleExportPayload {
  format: 'securo-categorization-rules'
  version: number
  rules: RuleExportItem[]
}

export interface RuleImportResponse {
  imported: number
  skipped: number
  overwritten: number
}

/** One matched transaction in a rule preview, with the category the draft rule
 * would leave it in. `will_change` is false when the rule matches but changes
 * nothing — usually a transaction that already has a category the draft keeps. */
export interface RulePreviewItem {
  id: string
  date: string
  description: string
  amount: number
  currency: string
  type: 'debit' | 'credit'
  current_category_id: string | null
  current_category_name: string | null
  new_category_id: string | null
  new_category_name: string | null
  will_change: boolean
}

export interface RulePreviewResponse {
  matched: number
  will_change: number
  /** False when the draft's flags mean saving it changes nothing right now —
   * an inactive rule, or one not being applied to existing transactions. */
  will_apply: boolean
  /** One window of the matches, newest first: `offset` through
   * `offset + limit`. More remain while `offset + sample.length < matched`. */
  sample: RulePreviewItem[]
  offset: number
}

export interface ImportLog {
  id: string
  user_id: string
  /** Null for an order import, which lands on holdings rather than an account. */
  account_id: string | null
  account_name: string | null
  /** Currency of the totals; null when the import has no account. */
  account_currency: string | null
  entity: 'transactions' | 'asset_orders'
  filename: string
  format: string
  transaction_count: number
  total_credit: number
  total_debit: number
  created_at: string
}

export interface ImportPreviewTransaction {
  description: string
  amount: number
  date: string
  type: 'debit' | 'credit'
  external_id?: string | null
  currency?: string | null
  fx_rate?: number | null
  payee_raw?: string | null
  category_name?: string | null
  suggested_category_id?: string | null
  suggested_category_name?: string | null
  excluded?: boolean
  category_id?: string | null
  force_uncategorized?: boolean
  notes?: string | null
}

export interface ImportReviewTransaction extends ImportPreviewTransaction {
  _id: string
  excluded: boolean
  selected_category_id?: string | null
}

export interface FailedRow {
  line_number: number
  description: string
  raw_value: string
  error_reason: string
}

export interface RecurringTransaction {
  id: string
  user_id: string
  account_id: string | null
  category_id: string | null
  description: string
  amount: number
  currency: string
  type: 'debit' | 'credit'
  frequency: 'monthly' | 'quarterly' | 'semiannual' | 'weekly' | 'biweekly' | 'yearly'
  weekend_adjustment: 'none' | 'previous_friday' | 'next_monday'
  day_of_month: number | null
  start_date: string
  end_date: string | null
  is_active: boolean
  auto_generate: boolean
  next_occurrence: string
  amount_primary: number | null
  fx_rate_used: number | null
  /**
   * `loan_payment`: pays the loan `transfer_account_id` from `account_id`.
   * `amount` is the whole payment, `interest_amount` its fixed interest share
   * (booked in `interest_category_id`); only the interest is an expense.
   */
  kind?: 'standard' | 'loan_payment'
  transfer_account_id?: string | null
  interest_amount?: number | string | null
  interest_category_id?: string | null
  /** Fork: the item's payee, drawn like a transaction's. */
  payee_id?: string | null
  payee_name?: string | null
  /** The payee's logo (provider's, else its website favicon), or null. */
  payee_logo_url?: string | null
  /** The icon the user picked for the payee; wins over the logo. */
  payee_icon?: string | null
}

// Fork: the Forecast page.
export type ForecastItemKind =
  | 'one_off'
  | 'repeating'
  | 'recurring_change'
  | 'income_change'
  | 'spending_change'
  | 'loan_change'
  // Fork: a plan for everyday spending (no scenario).
  | 'budget'
export type ForecastChangeMode = 'amount' | 'pause' | 'stop' | 'percent' | 'rate' | 'lump_sum'

export interface ForecastScenario {
  id: string
  name: string
  color: string
  is_enabled: boolean
  position: number
}

/** An expected payment (`scenario_id` null) or one change inside a scenario. */
export interface ForecastItem {
  id: string
  scenario_id: string | null
  kind: ForecastItemKind
  description: string
  /** Decimal string. Signed for income_change and spending_change (negative = less). */
  amount: string | number | null
  currency: string
  type: 'debit' | 'credit' | null
  date: string | null
  start_date: string | null
  end_date: string | null
  frequency: RecurringTransaction['frequency'] | null
  account_id: string | null
  /** Fork: an expected transfer's to-account (`account_id` is where it leaves). */
  transfer_account_id?: string | null
  category_id: string | null
  recurring_transaction_id: string | null
  change_mode: ForecastChangeMode | null
  /** The new interest rate for a loan_change in mode 'rate'. */
  percent: string | number | null
  /** loan_change 'rate': recalculate the repayment over the remaining term (true) or keep it. */
  recalculate_repayment?: boolean | null
  matched_transaction_id: string | null
  /** Fork: a repeating expected payment's matched occurrences. */
  matches?: ForecastItemMatch[]
  /** Fork: how many payments a repeating expected payment makes, start to end; null when open-ended. */
  occurrence_count?: number | null
}

/** Fork: the bank row that settled one occurrence of a repeating expected payment. */
export interface ForecastItemMatch {
  occurrence_date: string
  transaction_id: string
}

export interface ForecastPoint {
  date: string
  value: number
}

export interface ForecastLineSummary {
  end_balance: number
  lowest_balance: number
  lowest_date: string
}

export interface ForecastScenarioSeries {
  id: string
  name: string
  color: string
  points: ForecastPoint[]
  summary: ForecastLineSummary
  difference: number
}

export interface SpendingEstimate {
  category_id: string
  name: string
  color: string
  monthly_amount: number
}

/** A loan's balance at `date`, and what repayments since the previous point paid. */
export interface LoanPoint {
  date: string
  balance: number
  interest: number
  principal: number
}

export interface LoanLine {
  points: LoanPoint[]
  /** null: never paid off at this repayment. */
  payoff_date: string | null
  /** From today until payoff. */
  total_interest: number
  repayment: number
  /** Fork: balance month by month over the whole term, to the last payoff among the loan's lines. */
  term_points?: LoanPoint[]
}

export interface LoanScenarioLine extends LoanLine {
  scenario_id: string
  name: string
  color: string
  interest_saved: number | null
  payoff_change_days: number | null
}

export interface LoanForecast {
  account_id: string
  name: string
  currency: string
  balance: number
  rate: number | null
  frequency: string | null
  missing_details: boolean
  /** Owed at each history point, as far back as the forecast looks ahead, ending today. */
  actuals?: ForecastPoint[]
  base: LoanLine | null
  scenarios: LoanScenarioLine[]
}

/** Fork: one dated payment of a recurring schedule inside the forecast. */
export interface ForecastRecurringPayment {
  recurring_transaction_id: string
  date: string
  description: string
  amount: number
  type: 'debit' | 'credit'
  currency: string
  account_id: string | null
}

export interface ForecastResponse {
  currency: string
  interval: string
  start_date: string
  end_date: string
  starting_balance: number
  include_spending: boolean
  /** Fork: the accounts the line covers (all cash and card accounts, or the chosen ones). */
  account_ids?: string[]
  /** The real balance from (today − horizon) to today; the last point is today's starting balance. */
  actuals?: ForecastPoint[]
  base: ForecastPoint[]
  summary: ForecastLineSummary
  scenarios: ForecastScenarioSeries[]
  spending_estimates: SpendingEstimate[]
  loans?: LoanForecast[]
  recurring_payments?: ForecastRecurringPayment[]
}

export interface ProjectedTransaction {
  recurring_id: string
  account_id: string | null
  description: string
  amount: number
  amount_primary: number | null
  currency: string
  type: 'debit' | 'credit'
  date: string
  category_id: string | null
  category_name: string | null
  category_icon: string | null
  category_color: string | null
  /** A recurring loan payment: `amount` is the whole payment, only the interest is an expense. */
  kind?: 'standard' | 'loan_payment'
  transfer_account_id?: string | null
  interest_amount?: number | null
  interest_amount_primary?: number | null
  interest_category_id?: string | null
  interest_category_name?: string | null
  interest_category_icon?: string | null
  interest_category_color?: string | null
}

export interface TransactionCalendarItem {
  kind: 'actual' | 'projected'
  id: string | null
  recurring_id: string | null
  date: string
  description: string
  amount: number
  amount_primary: number | null
  currency: string
  type: 'debit' | 'credit'
  account_id: string | null
  account_name: string | null
  category_id: string | null
  category_name: string | null
  category_icon: string | null
  category_color: string | null
  /** The payee's badge, drawn before the category icon. */
  payee_logo_url?: string | null
  payee_icon?: string | null
  status: string | null
  source: string | null
  transfer_pair_id: string | null
  /** The account the other leg of a linked transfer sits in. */
  transfer_counterpart_account_id?: string | null
  /** Drawable summaries of both transfer accounts, closed ones included. */
  transfer_account?: TransferAccountRef | null
  transfer_counterpart_account?: TransferAccountRef | null
  is_transfer: boolean
  is_ignored: boolean
  exclude_from_pnl: boolean
}

export interface TransactionCalendarDay {
  date: string
  in_month: boolean
  ending_balance: number
  // Combined totals kept for backwards compatibility.
  income: number
  expense: number
  transfer_net: number
  actual_income: number
  actual_expense: number
  actual_transfer_net: number
  projected_income: number
  projected_expense: number
  projected_transfer_net: number
  actual_count: number
  projected_count: number
  has_income: boolean
  has_expense: boolean
  has_transfer: boolean
  items: TransactionCalendarItem[]
}

export interface TransactionCalendarResponse {
  month: string
  currency: string
  account_ids: string[] | null
  days: TransactionCalendarDay[]
}

export interface DashboardSummary {
  total_balance: Record<string, number>
  total_balance_primary: number
  projected_balance: Record<string, number>
  projected_balance_primary: number
  balance_date: string
  monthly_income: number
  monthly_expenses: number
  monthly_income_primary: number
  monthly_expenses_primary: number
  projected_income?: number
  projected_expenses?: number
  projected_income_primary?: number
  projected_expenses_primary?: number
  accounts_count: number
  pending_categorization: number
  pending_categorization_amount: number
  assets_value: Record<string, number>
  assets_value_primary: number
  primary_currency: string
  // Net pending balance from group splits in primary currency.
  // Negative = net liability, positive = net receivable. Already
  // accounts for partial settlements.
  pending_shares_net: number
}

export interface SpendingByCategory {
  category_id: string | null
  category_name: string
  category_icon: string
  category_color: string
  total: number
  projected_total: number
  percentage: number
}

export interface MonthlyTrend {
  month: string
  income: number
  expenses: number
}

export interface DailyBalance {
  day: number
  balance: number | null
}

export interface BalanceHistory {
  current: DailyBalance[]
  previous: DailyBalance[]
}

export interface Budget {
  id: string
  user_id: string
  category_id: string
  amount: number
  month: string
  is_recurring: boolean
}

export interface BudgetVsActual {
  category_id: string
  category_name: string
  category_icon: string
  category_color: string
  group_id: string | null
  group_name: string | null
  budget_amount: number | null
  actual_amount: number
  projected_amount: number
  prev_month_amount: number
  projected_prev_month_amount: number
  percentage_used: number | null
  is_recurring: boolean
}

export interface Asset {
  id: string
  user_id: string
  name: string
  type: string
  currency: string
  units: number | null
  valuation_method: string
  purchase_date: string | null
  purchase_price: number | null
  sell_date: string | null
  sell_price: number | null
  growth_type: string | null
  growth_rate: number | null
  growth_frequency: string | null
  growth_start_date: string | null
  is_archived: boolean
  position: number
  current_value: number | null
  current_value_primary: number | null
  gain_loss: number | null
  gain_loss_primary: number | null
  value_count: number
  source: string
  connection_id: string | null
  // The synced account whose balance already carries this holding; net
  // worth counts the account, not the holding.
  account_external_id?: string | null
  isin: string | null
  maturity_date: string | null
  group_id: string | null
  ticker: string | null
  ticker_exchange: string | null
  last_price: number | null
  last_price_at: string | null
  logo_url: string | null
  // Ledger-derived (issue #235): weighted-average cost per unit (preço médio),
  // cost basis of held units, cumulative realized gain, and whether the holding
  // is driven by the transactions ledger.
  average_price: number | null
  total_invested: number | null
  realized_gain: number | null
  transaction_count: number
  // Equity against linked loans (liabilities phase 3). All in the asset's
  // currency; the _primary copies are set only when that differs from the
  // user's currency. Display only: never added to any total.
  linked_loan_ids: string[]
  linked_loans: AssetLinkedLoan[]
  linked_debt: number | null
  linked_debt_primary: number | null
  equity: number | null
  equity_primary: number | null
}

/** A loan account secured against an asset. */
export interface AssetLinkedLoan {
  id: string
  name: string
  currency: string
  /** The loan's balance in its own currency (negative = owed). */
  balance: number
  /** Amount owed, converted to the asset's currency. */
  debt: number
}

/**
 * A recorded payment on a manual loan (backend `LoanPaymentRead`).
 *
 * `total` is the cash leg's amount, `interest` the interest row's amount (0
 * without one) and `principal = total - interest`. All three are derived from
 * the payment's rows for display; nothing stores principal. The API sends them
 * as decimal strings; `lib/api.ts` turns them into numbers.
 */
export interface LoanPayment {
  id: string
  loan_account_id: string
  cash_account_id: string
  cash_transaction_id: string
  loan_transaction_id: string
  interest_transaction_id: string | null
  interest_category_id: string | null
  /** False when the payment paired a debit that was already on the paying
   *  account (synced or imported): its date and total are then the bank's. */
  cash_leg_created: boolean
  recurring_transaction_id: string | null
  date: string
  description: string
  currency: string
  total: number
  interest: number
  principal: number
  created_at: string
}

/** Body of `POST /api/loan-payments`. Amounts are two-decimal strings. Give
 *  either `cash_account_id` (writes the cash debit; `date` required) or
 *  `existing_transaction_id` (pairs that debit; `date` defaults to its date). */
export interface LoanPaymentCreate {
  loan_account_id: string
  date?: string
  total_amount: string
  interest_amount: string
  interest_category_id?: string | null
  cash_account_id?: string
  existing_transaction_id?: string
  description?: string
  notes?: string
}

/** Body of `PATCH /api/loan-payments/{id}`. Omitted fields keep their value; a
 *  payment that paired a bank row refuses a new date or total. */
export interface LoanPaymentUpdate {
  date?: string
  total_amount?: string
  interest_amount?: string
  interest_category_id?: string | null
  description?: string
}

/** One month of `GET /api/accounts/{id}/loan-summary`, read from the ledger. */
export interface LoanSummaryMonth {
  /** YYYY-MM */
  month: string
  /** Credits on the loan: repayments. */
  paid: number
  /** Debits on the loan that count in P&L (interest and fees). */
  interest: number
  /** How much the amount owed fell. */
  principal_reduction: number
}

export interface LoanSummary {
  account_id: string
  currency: string
  /** False for loans that sync their own rows: no manual payment form. */
  manual_payments_allowed: boolean
  months: LoanSummaryMonth[]
  total_paid: number
  total_interest: number
  total_principal_reduction: number
}

/** One order read from a broker CSV, before it reaches a holding. */
export interface AssetOrderImport {
  row: number
  ticker: string
  date: string
  kind: 'buy' | 'sell'
  quantity: number
  price: number
  fee: number
  currency: string | null
  name: string | null
  notes: string | null
  external_id: string | null
}

export interface AssetImportRowError {
  row: number
  reason: string
  ticker: string | null
  detail: string | null
}

export interface AssetImportWarning {
  ticker: string
  reason: string
  wallet: string | null
}

export interface AssetImportPreview {
  orders: AssetOrderImport[]
  errors: AssetImportRowError[]
  warnings: AssetImportWarning[]
  csv_columns: string[]
  parse_error: string | null
  holdings_created: number
  holdings_matched: number
  skipped: number
}

export interface AssetImportResult {
  imported: number
  skipped: number
  holdings_created: number
  holdings_matched: number
  errors: AssetImportRowError[]
  warnings: AssetImportWarning[]
}

export interface AssetTransaction {
  id: string
  asset_id: string
  kind: 'buy' | 'sell'
  quantity: number
  price: number
  fee: number
  date: string
  source: string
  notes: string | null
  asset_name: string | null
  ticker: string | null
  currency: string | null
  logo_url: string | null
}

export interface MarketSymbolMatch {
  symbol: string
  name: string | null
  exchange: string | null
  quote_type: string | null
}

export interface MarketSymbolQuote {
  symbol: string
  name: string | null
  exchange: string | null
  currency: string
  price: number
  quote_type: string | null
}

export interface AssetGroup {
  id: string
  user_id: string
  name: string
  icon: string
  color: string
  position: number
  source: string
  connection_id: string | null
  institution_name: string | null
  asset_count: number
  current_value: number
  current_value_primary: number
}

export interface AssetValue {
  id: string
  asset_id: string
  amount: number
  date: string
  source: string
}

// A goal's periodic contribution target recurs weekly, fortnightly
// (`biweekly`, the recurring-transaction word) or monthly.
export type ContributionPeriod = 'weekly' | 'biweekly' | 'monthly'

export interface ContributionPeriodResult {
  start: string
  end: string
  contributed: number
  target: number
  // met / over: target reached; short: a finished period that fell short;
  // pending: the current period, not reached yet.
  status: 'met' | 'over' | 'short' | 'pending'
  current: boolean
}

export interface ContributionProgress {
  period: ContributionPeriod
  target: number
  current: ContributionPeriodResult
  history: ContributionPeriodResult[] // the last ten finished periods and the current one, oldest first
  streak: number
  // Ahead or behind the plan since money last left the account (its first
  // deposit when it never has). All null while nothing has gone in or out.
  since?: string | null
  saved_since?: number | null
  planned_since?: number | null
  ahead_by?: number | null // saved_since - planned_since; negative: behind
}

export interface ContributionSuggestion {
  amount: number
  period: ContributionPeriod
  currency: string
  source: 'recurring' | 'history'
  recurring_transaction_id: string | null
  description: string | null
}

export interface Goal {
  id: string
  user_id: string
  name: string
  // Null for an ongoing savings goal that only has a periodic target.
  target_amount: number | null
  contribution_amount: number | null
  contribution_period: ContributionPeriod | null
  current_amount: number
  currency: string
  target_amount_primary: number | null
  current_amount_primary: number | null
  target_date: string | null
  tracking_type: 'manual' | 'account' | 'asset' | 'asset_group' | 'net_worth'
  account_id: string | null
  asset_id: string | null
  asset_group_id: string | null
  status: 'active' | 'completed' | 'paused' | 'archived'
  icon: string | null
  color: string | null
  position: number
  metadata_json: Record<string, unknown> | null
  created_at: string
  updated_at: string
  percentage: number
  monthly_contribution: number | null
  on_track: 'ahead' | 'on_track' | 'behind' | 'overdue' | 'achieved' | null
  // Contributed vs the periodic target; only for a goal on an account.
  contribution_progress: ContributionProgress | null
  account_name: string | null
  asset_name: string | null
  asset_group_name: string | null
}

export interface GoalSummary {
  id: string
  name: string
  target_amount: number | null
  contribution_amount: number | null
  contribution_period: ContributionPeriod | null
  period_contributed: number | null
  period_status: ContributionPeriodResult['status'] | null
  contribution_streak: number | null
  current_amount: number
  currency: string
  target_date: string | null
  status: string
  icon: string | null
  color: string | null
  percentage: number
  monthly_contribution: number | null
  on_track: string | null
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  limit: number
}

// Income / expense / net totals for all transactions matching the active
// filters (issue #185) — accompanies the paginated /transactions response.
export interface TransactionsSummary {
  income: number
  expense: number
  net: number
  // Absolute total of everything excluded from income/expense for the same
  // rows — transfers, treat_as_transfer categories and ignored items (#242).
  excluded: number
  currency: string
}

export interface PaginatedTransactions extends PaginatedResponse<Transaction> {
  summary?: TransactionsSummary
}

// Reports (universal schema for all report types)
export interface ReportBreakdown {
  key: string
  label: string
  value: number
  color: string
}

export interface ReportSummary {
  primary_value: number
  change_amount: number
  change_percent: number | null
  breakdowns: ReportBreakdown[]
}

export interface ReportDataPoint {
  date: string
  value: number
  breakdowns: Record<string, number>
  change: number | null
  composition?: ReportCompositionItem[]
}

export interface ReportMeta {
  type: string
  series_keys: string[]
  currency: string
  interval: string
  forecast_start_date?: string | null
  baseline_active?: boolean
  baseline_lookback_days?: number | null
}

export interface ReportCompositionItem {
  key: string
  label: string
  value: number
  color: string
  group: string
}

export interface CategoryTrendItem {
  key: string
  label: string
  color: string
  total: number
  group: string
  series: ReportDataPoint[]
}

export interface Attachment {
  id: string
  transaction_id: string
  filename: string
  content_type: string
  size: number
  created_at: string
}

export interface ReportResponse {
  summary: ReportSummary
  trend: ReportDataPoint[]
  meta: ReportMeta
  composition: ReportCompositionItem[]
  category_trend: CategoryTrendItem[]
}

// --- Invoices -------------------------------------------------------------
// The ledger of what clients owe. Only reachable from a business
// workspace: the module resolver leaves `invoices` out of a personal
// workspace's `enabled_modules`, so nothing below is ever fetched there.

/** A decision a human took. Never PATCHed directly — each one has its own
 *  endpoint, because a status that changed always has a reason. */
export type InvoiceStatus = 'draft' | 'open' | 'void' | 'uncollectible'

/** Which side of the ledger a document sits on. Everything the UI writes
 *  today is a receivable; `payable` exists so supplier documents have
 *  somewhere to land without a migration when that path arrives. */
export type InvoiceDirection = 'receivable' | 'payable'

/** What the UI renders. The three terminal decisions above, plus the four
 *  facts the server computes from allocations and the due date. Nothing
 *  here is stored in a column. */
export type InvoiceState =
  | 'draft'
  | 'open'
  | 'partial'
  | 'paid'
  | 'overdue'
  | 'void'
  | 'uncollectible'

export interface InvoiceLine {
  id: string
  /** Where the line came from, when it came from the catalog. The
   *  values below are still the line's own copy. */
  product_id: string | null
  price_id: string | null
  fiscal_refs: Record<string, string> | null
  description: string
  quantity: string
  unit: string | null
  unit_price: string
  tax_rate: string | null
  total: string
  position: number
}

export interface InvoiceLineInput {
  description: string
  quantity: string
  /** What the quantity counts — hours, words, pieces. Free text, because
   *  a list here would be a guess about somebody else's trade. */
  unit?: string | null
  unit_price: string
  tax_rate?: string | null
  /** Set when the line was filled from a product. Kept when the person
   *  then edits the values: it is still that product, at their price. */
  product_id?: string | null
  price_id?: string | null
  /** Fiscal references for the line. Filled from the product by the
   *  server when omitted. */
  fiscal_refs?: Record<string, string> | null
}

// ---------------------------------------------------------------------------
// Catalog
// ---------------------------------------------------------------------------

export type ProductKind = 'service' | 'product'
export type PriceBilling = 'one_time' | 'recurring'

export interface ProductPrice {
  id: string
  product_id: string
  currency: string
  unit_price: string
  tax_rate: string | null
  billing: PriceBilling
  interval: InvoiceScheduleFrequency | null
  nickname: string | null
  /** A name of the workspace's own choosing, unique among its prices. */
  lookup_key: string | null
  active: boolean
  external_source: string | null
  external_id: string | null
  created_at: string
}

/** A fiscal reference the workspace's jurisdiction suggests on a product. */
export interface ProductFieldSpec {
  key: string
  label_key: string
  /** Which product kinds it applies to; empty means both. */
  kinds: ProductKind[]
}

export interface Product {
  id: string
  name: string
  description: string | null
  kind: ProductKind
  unit: string | null
  active: boolean
  origin: string
  external_source: string | null
  external_id: string | null
  custom_fields: Record<string, string> | null
  /** Fiscal references keyed as the jurisdiction suggests (`ncm`,
   *  `service_code`, `hs_code`...); any key is accepted. */
  fiscal_refs: Record<string, string> | null
  prices: ProductPrice[]
  created_at: string
  /** Derived by the server: how many invoices name this product. */
  invoice_count: number
}

export interface InstallmentInput {
  due_date: string
  amount: string
  label?: string | null
}

export type InstallmentState = 'open' | 'partial' | 'paid' | 'overdue' | 'draft' | 'void' | 'uncollectible'

export interface InvoiceInstallment {
  id: string
  position: number
  label: string | null
  due_date: string
  amount: string
  /** Derived: how much of it the settled money covers, first-to-last. */
  settled: string
  state: InstallmentState
}

export type DeductionKind = 'withholding_tax' | 'gateway_fee' | 'fx_difference' | 'other'

/** Debt closed without money: tax withheld, a fee kept. Counts towards
 *  settled, never towards received. */
export interface InvoiceDeduction {
  id: string
  kind: DeductionKind
  tax_kind: string | null
  amount: string
  note: string | null
  transaction_id: string | null
  deducted_at: string
}

export interface InvoiceAllocation {
  id: string
  transaction_id: string | null
  credit_note_id: string | null
  amount: string
  method: string
  allocated_at: string
  transaction: {
    id: string
    description: string | null
    date: string | null
    amount: string | null
  } | null
}

/** What a filed document proves. Roles, not file types — a nota fiscal,
 *  a facture and a Rechnung are all `fiscal`, and each locale names it. */
export type InvoiceAttachmentKind = 'bill' | 'fiscal' | 'receipt' | 'contract' | 'other'

export interface InvoiceAttachment {
  id: string
  invoice_id: string
  /** The system that produced or delivered the file — a payment
   *  provider, a fiscal-document integration, a mailbox. Null when a
   *  person uploaded it here. */
  source: string | null
  /** Its id in that system, when it has one. */
  external_id: string | null
  kind: InvoiceAttachmentKind
  /** True on the one file that *is* the document. Downloading the invoice
   *  hands this file over instead of a page drawn from our own fields. */
  is_primary: boolean
  document_number: string | null
  issued_at: string | null
  filename: string
  content_type: string
  size: number
  created_at: string
}

export interface Invoice {
  id: string
  payee_id: string | null
  payee: { id: string; name: string } | null
  document_type: string
  direction: InvoiceDirection
  origin: string
  external_source: string | null
  external_id: string | null
  number: number | null
  series: string | null
  /** The name an imported document arrived with, reproduced verbatim.
   *  Null on anything we wrote, which our own counter names instead. */
  external_number: string | null
  status: InvoiceStatus
  state: InvoiceState
  issue_date: string
  due_date: string
  /** The accrual date — competência / fait générateur / Leistungsdatum.
   *  Defaults to `issue_date` and diverges when work was delivered in a
   *  different period from the one it was billed in. */
  competence_date: string | null
  sent_at: string | null
  currency: string
  subtotal: string
  discount: string
  tax_total: string
  total: string
  amount_paid: string
  /** Settled without cash. Not part of `amount_paid`. */
  amount_deducted: string
  balance: string
  days_overdue: number
  /** The next date money is late after; null once nothing is owed. */
  next_due_date: string | null
  notes: string | null
  internal_notes: string | null
  custom_fields: Record<string, string> | null
  /** Frozen at issuance: issuer, counterparty and labels as they were.
   *  Rendered instead of live settings so changing a logo never rewrites
   *  a document the client already received.
   *
   *  `unknown` rather than `any`: the shape is the server's and it grows
   *  (locale and payment details joined it after the first version), so
   *  every reader asserts the one field it wants instead of the type
   *  quietly promising all of them. */
  snapshot: Record<string, unknown> | null
  /** Present once a shareable link exists. Null until someone asks for
   *  one, and null again once revoked. */
  share_token: string | null
  /** Which agreement and period this invoice answers for, when it was
   *  born from or linked to one. Provenance only: nothing about the
   *  money reads these. */
  schedule_id: string | null
  schedule: { id: string; name: string; frequency: InvoiceScheduleFrequency; status: InvoiceScheduleStatus } | null
  sequence: number | null
  period_start: string | null
  period_end: string | null
  lines: InvoiceLine[]
  allocations: InvoiceAllocation[]
  installments: InvoiceInstallment[]
  deductions: InvoiceDeduction[]
  created_at: string
}

// ---------------------------------------------------------------------------
// Recurring invoices
// ---------------------------------------------------------------------------

/** Decisions a person took about an agreement. `past_due` is not one of
 *  them: it is derived from the agreement's invoices. */
export type InvoiceScheduleStatus = 'active' | 'paused' | 'ended'
export type InvoiceScheduleFrequency =
  | 'weekly'
  | 'biweekly'
  | 'monthly'
  | 'quarterly'
  | 'semiannual'
  | 'yearly'
export type InvoiceScheduleEndType = 'never' | 'on_date' | 'after_count'
export type InvoiceScheduleEndReason =
  | 'canceled_by_client'
  | 'canceled_by_us'
  | 'completed'
  | 'unpaid'
  | 'other'

/** What the agreement says from a date on. One row per price change. */
export interface InvoiceScheduleTerm {
  id: string
  effective_from: string
  lines: InvoiceLineInput[]
  discount: string
  subtotal: string
  tax_total: string
  total: string
  created_at: string
}

export interface InvoiceSchedule {
  id: string
  name: string
  payee_id: string | null
  payee: { id: string; name: string } | null
  origin: string
  external_source: string | null
  external_id: string | null
  status: InvoiceScheduleStatus
  pause_reason: 'manual' | 'failures' | null
  ended_at: string | null
  end_reason: InvoiceScheduleEndReason | null
  frequency: InvoiceScheduleFrequency
  start_date: string
  end_type: InvoiceScheduleEndType
  end_date: string | null
  end_count: number | null
  payment_terms_days: number | null
  currency: string
  notes: string | null
  custom_fields: Record<string, string> | null
  next_sequence: number
  last_generated_at: string | null
  consecutive_failures: number
  terms: InvoiceScheduleTerm[]
  created_at: string
  /** Derived by the server on every read; never stored. */
  current_term: InvoiceScheduleTerm | null
  next_term: InvoiceScheduleTerm | null
  monthly_amount: string
  next_period_start: string | null
  invoice_count: number
  amount_invoiced: string
  amount_paid: string
  past_due_count: number
}

export interface InvoiceScheduleCurrencySummary {
  currency: string
  monthly_recurring: string
  active_count: number
  ended_recently_count: number
  monthly_lost: string
  past_due_count: number
}

export interface InvoiceScheduleSummary {
  active_count: number
  paused_count: number
  ended_count: number
  by_currency: InvoiceScheduleCurrencySummary[]
}

export interface InvoiceSchedulePeriod {
  sequence: number
  period_start: string
  period_end: string
  taken: boolean
}

export interface InvoiceAgingBuckets {
  current: string
  d1_30: string
  d31_60: string
  d61_90: string
  d90_plus: string
}

export interface InvoiceSummary {
  outstanding: string
  overdue_amount: string
  overdue_count: number
  received_this_month: string
  buckets: InvoiceAgingBuckets
  upcoming: Invoice[]
}

export interface InvoiceCustomFieldDef {
  key: string
  label: string
  required?: boolean
}

export interface InvoiceTemplate {
  /** Overrides only. Anything left out falls back to the pack for the
   *  issuer's language, resolved server-side. */
  labels?: Record<string, string>
  custom_fields?: InvoiceCustomFieldDef[]
}

export interface InvoiceSettings {
  /** A starting point that fills the next three fields, not a mode: each
   *  one stays individually overridable afterwards. */
  preset: 'tracking' | 'document'
  document_required: boolean
  initial_state: 'draft' | 'open'
  tax_fields: 'hidden' | 'optional' | 'required'
  default_payment_terms_days: number
  number_prefix: string | null
  series: string | null
  next_number: number
  /** The uploaded mark, addressed by an id that never changes for a
   *  given file. Null when none was uploaded. */
  logo_id: string | null
  issuer_display_name: string | null
  footer_note: string | null
  /** Free text: a Pix key, an IBAN, a routing number. Shown on the
   *  document; never parsed. */
  payment_details: string | null
  accent_color: string | null
  template: InvoiceTemplate | null
}

/** The invoice resolved into a document: what the PDF prints and what the
 *  preview shows, from one definition on the server. Nothing here is
 *  recomputed on this side — a second implementation is how the two
 *  would come to disagree. */
export interface InvoiceDocumentParty {
  name: string | null
  legal_name?: string | null
  address: string | null
  email?: string | null
  /** Already masked server-side, because the PDF has no frontend to ask. */
  tax_ids: { label: string; value: string }[]
}

export interface InvoiceDocumentLine {
  description: string
  quantity: string
  unit: string | null
  unit_price: string
  total: string
  tax_rate: string | null
}

export interface InvoiceDocumentPayload {
  number: string | null
  status: InvoiceStatus
  state: InvoiceState
  issue_date: string
  due_date: string
  currency: string
  subtotal: string
  discount: string
  tax_total: string
  total: string
  amount_paid: string
  /** Settled without money arriving: tax withheld, a fee kept. */
  amount_deducted: string
  balance: string
  issuer: InvoiceDocumentParty
  client: InvoiceDocumentParty
  lines: InvoiceDocumentLine[]
  labels: Record<string, string>
  accent_color: string
  logo_url: string | null
  payment_details: string | null
  notes: string | null
  footer_note: string | null
  custom_fields: { label: string; value: string }[]
  /** On a payable the parties are already swapped server-side; this is
   *  carried so the page can say "received" instead of "issued". */
  direction: InvoiceDirection
  /** False when the invoice is only tracking money — the common case
   *  where the fiscal document was issued somewhere else entirely. */
  has_line_items: boolean
  /** Set when a real document was filed under this invoice. When it is,
   *  that file is the document and the page below is only a summary of
   *  it — nothing here needs redrawing. */
  source_file: { id: string; filename: string; content_type: string } | null
  /** The dates the money is expected on, when more than one. */
  installments: { label: string | null; due_date: string; amount: string }[]
}

export interface IssuerTaxId {
  kind: string
  value: string
}

export interface IssuerProfile {
  legal_name: string | null
  address: string | null
  tax_jurisdiction: string | null
  tax_ids: IssuerTaxId[]
}

export interface InvoiceShareLink {
  token: string
  /** A path, not a URL: only the browser reliably knows the public origin. */
  path: string
}

/** What the filter bar needs to render itself: which years have
 *  invoices, and how many sit in each state for the selected year. */
export interface InvoiceFacets {
  years: number[]
  counts: {
    all: number
    open: number
    overdue: number
    paid: number
    draft: number
  }
}

// ---------------------------------------------------------------------------
// Reconciliation
// ---------------------------------------------------------------------------

/** The signals matching consults, as the API sends them.
 *
 *  Deliberately not an open-ended condition tree like the categorization
 *  rules: matching runs on a fixed set of signals, and a form over exactly
 *  those is honest about what the engine can actually look at. */
/** Which moment a rule runs at.
 *
 *  `money_arrives`: a payment lands and we look for the promise it
 *  answers; the promise came first and was waiting.
 *  `invoice_issued`: a document is written and we look back at money that
 *  arrived before it existed, the client who pays and lets the nota
 *  follow. Weaker evidence: that money already had a life of its own.
 *  `both`: the rule trusts either. */
export type Trigger = 'money_arrives' | 'invoice_issued' | 'both'

export interface ReconciliationConditions {
  // -- Which money the rule is written for. Every one of these decides
  //    whether the rule is consulted at all, before any comparison. Absent
  //    means "no limit", never "none".
  /** Only money in these bank accounts. */
  accounts?: { in: string[] }
  /** Only money from these clients. Different from `counterparty`, which
   *  asks whether the payer is the one named on the invoice. */
  payees?: { in: string[] }
  /** Only money coming in, or only money going out. */
  direction?: 'any' | 'credit' | 'debit'
  /** What the statement line has to say, or must not. Case-insensitive. */
  /** One word, or several meaning *any of them*. Stored as written, so a
   *  rule naming one gateway stays a string and does not read as changed. */
  text?: { contains?: string | string[]; not_contains?: string | string[] }

  // -- How closely the pair has to fit.
  counterparty?: 'any' | 'same_payee'
  amount?: {
    /** `partial` is what makes two transactions on one invoice reachable:
     *  every other mode compares against the whole outstanding balance,
     *  and half of it is simply not that. */
    match: 'exact' | 'tolerance' | 'ratio' | 'partial' | 'set'
    percent?: string
    /** For `set`: how many invoices one payment may cover. */
    max_invoices?: number
    epsilon?: string
    ratios?: string
    difference_kind?: string
    /** For `partial`: the smallest fraction of the balance worth
     *  offering, so a token payment is not proposed as an instalment. */
    min_ratio?: string
    /** For `partial`: the largest, above which the gap is a fee or a
     *  withholding rather than an instalment. */
    max_ratio?: string
    /** Bounds on the movement itself, not on how close it is to the
     *  promise: "never link anything over ten thousand on its own". */
    min?: string
    max?: string
  }
  /** Asymmetric on purpose: late is measured from the due date, early from
   *  the day the promise was written. */
  date?: { before_days: number; after_days: number }
  description_similarity?: { min: string }
  currency?: {
    /** Whether a movement may settle a promise held in another currency. */
    conversion: 'reject' | 'allow'
    /** Only these currency codes. */
    in?: string[]
    /** Only currencies other than the workspace's own: what somebody
     *  means by "check anything that is not in our money". */
    foreign?: boolean
  }
  same_account?: boolean
  /** The defining condition of a transfer: the two legs are on different
   *  accounts. The inverse of `same_account`, and its own key so a rule
   *  reads as a list of things that must be true. */
  different_account?: boolean
  /** Only pairs where one of the two legs sits on an account of this
   *  kind. Either leg is enough: a rule that exists to be careful about
   *  credit cards has to fire whichever end the card is on. */
  account_types?: ('checking' | 'savings' | 'credit_card' | 'investment' | 'wallet' | 'loan')[]
  /** One side's statement text has to name the other side's account. The
   *  signal that tells two same-day transfers of the same amount apart,
   *  when one line reads "To FORTUNEO ACCOUNT" and the other does not. */
  account_name_in_description?: boolean
  /** How to separate candidates that all fit. `closest_date` takes the
   *  nearest in time and only gives up when nothing separates them, which
   *  is different from `unique_candidate` refusing whenever there is more
   *  than one. */
  tie_break?: 'closest_date'
  unique_candidate?: boolean
}

export interface ReconciliationRule {
  id: string
  node: string
  /** Set only for a rule the workspace wrote. A shipped rule keeps its
   *  translated name, which would otherwise freeze in one language the day
   *  somebody edited a threshold. */
  name?: string | null
  origin: 'default' | 'custom'
  /** Whether this workspace departed from what we ship. Drives the "you
   *  changed this" mark and the offer to put it back. */
  customised: boolean
  enabled: boolean
  outcome: 'link' | 'suggest'
  trigger: Trigger
  when: ReconciliationConditions
  position: number
}

export interface ReconciliationNode {
  node: string
  /** Whether the set is reachable for this workspace at all. The invoice
   *  rules mean nothing where the module is off. */
  active: boolean
  rules: ReconciliationRule[]
  /** Rules we ship that this workspace threw away. Sent so the page can
   *  offer them back: a shipped rule leaves a tombstone rather than a
   *  hole, so we still know its name, and without this the delete is a
   *  trap, because the row is gone and there is nothing left to click. */
  discarded: { id: string; node: string }[]
}

/** A matching policy as a file. Ids are resolved to names on the way out
 *  and looked up again on the way in: a UUID means nothing in another
 *  database. */
export interface ReconciliationPolicyFile {
  format: string
  version: number
  policy_version: number
  nodes: {
    node: string
    rules: {
      id: string
      origin: string
      name: string | null
      enabled: boolean
      outcome: string
      trigger: string
      when: Record<string, unknown>
    }[]
    discarded: string[]
  }[]
}

export interface ReconciliationRulePatch {
  enabled?: boolean
  outcome?: 'link' | 'suggest'
  trigger?: Trigger
  when?: ReconciliationConditions
  position?: number
}

export interface ReconciliationRuleDraft {
  node: string
  name: string
  outcome: 'link' | 'suggest'
  trigger?: Trigger
  when: ReconciliationConditions
  enabled?: boolean
  position?: number | null
}

/** One match the engine was not confident enough to make on its own.
 *
 *  `scores` is a per-signal breakdown rather than one number: "78% sure" is
 *  not something anyone can check, while "the amount is exact and the date
 *  is four days out" tells a person exactly where to look. */
export interface ReconciliationSuggestion {
  id: string
  node: string
  strategy_id: string
  expectation_kind: 'invoice' | 'recurring' | 'transaction'
  expectation_id: string
  expectation_label?: string | null
  amount: string
  scores: {
    strategy?: string
    description?: number
    amount_expected?: string
    amount_moved?: string
    amount_exact?: boolean
    days_apart?: number
    same_counterparty?: boolean
    currency?: string
  }
  status: 'pending' | 'accepted' | 'declined' | 'expired'
  created_at: string
  /** Everything this one question covers. A single entry for the ordinary
   *  case; several when one payment is offered against several invoices,
   *  which is answered whole or not at all. */
  covers: {
    expectation_kind: 'invoice' | 'recurring' | 'transaction'
    expectation_id: string
    label?: string | null
    amount: string
  }[]
  transaction?: {
    id: string
    description?: string | null
    amount: string
    currency?: string | null
    date: string
    type: string
    /** Which account the money moved on. With transfers in the queue the
     *  question is about accounts, so a row naming only the other side
     *  leaves the reader to work out which of theirs this one is. */
    account_id?: string | null
  } | null
}

/** One thing matching did.
 *
 *  `linked` means the rules did it on their own; `accepted` means a person
 *  did, by answering a question. They are never both written for one act:
 *  the allocation an acceptance produces is its consequence, not a second
 *  event, because the whole stream is organised around that one line. */
export interface ReconciliationHistoryEvent {
  id: string
  at: string
  action: 'linked' | 'suggested' | 'accepted' | 'declined' | 'expired' | 'unlinked'
  expectation_kind: 'invoice' | 'recurring' | 'transaction'
  expectation_id: string
  expectation_label?: string | null
  amount: string
  /** What `amount` is denominated in, resolved from the promise. */
  currency?: string | null
  strategy_id?: string | null
  /** Null means the rules acted on their own. */
  user_id?: string | null
  transaction_id?: string | null
  transaction_description?: string | null
}

// Fork: the Insights page's monthly briefing.
export interface InsightCard {
  kind:
    | 'category_month' | 'trend_cooling' | 'trend_rising' | 'savings_rate' | 'price_up' | 'price_down'
    | 'payee_new' | 'payee_stopped' | 'small_burst' | 'income_change' | 'payee_switch' | 'habit_ended'
  direction: 'better' | 'worse'
  params: Record<string, string | number | boolean | { name: string; diff: number }[]>
  series: number[]
  category_id: string | null
  payee_id: string | null
  link_from: string | null
  link_to: string | null
}

export interface InsightsBriefing {
  month: string
  currency: string
  first_month: string | null
  last_month: string
  everyday_spend: number
  usual_spend: number
  income: number
  savings_rate: number | null
  savings_rate_before: number | null
  one_off_count: number
  one_off_total: number
  one_off_threshold: number
  categories: {
    category_id: string | null
    name: string
    amount: number
    usual: number
    diff: number
    usual_since: string | null
    drivers: { payee_id: string | null; payee: string; diff: number }[]
  }[]
  excluded: { category_id: string | null; name: string; reason: 'one_off' | 'more_payments' | 'fewer_payments' }[]
  better: InsightCard[]
  worse: InsightCard[]
}
