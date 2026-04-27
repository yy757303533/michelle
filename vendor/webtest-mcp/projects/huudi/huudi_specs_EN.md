# Huudi Project Specifications

## Document Information
- Version: V1.0.0
- Status: Production
- Created Date: 2024

---

## Table of Contents
1. [Project Overview](#1-project-overview)
2. [User Registration and Sign In](#2-user-registration-and-sign-in)
3. [KYC Verification](#3-kyc-verification)
4. [Project Management](#4-project-management)
5. [Investment Flow](#5-investment-flow)
6. [Backoffice Management](#6-backoffice-management)
7. [Finance and Cashflow](#7-finance-and-cashflow)
8. [Static Pages](#8-static-pages)
9. [Q&A and Feedback](#9-qa-and-feedback)
10. [Algorithm Rules](#10-algorithm-rules)

---

## 1. Project Overview

### 1.1 Project Milestones
Each project has the following milestones:
1. Project goes live
2. Project funding collection ends
3. Early reimbursement period ends
4. All the royalties payment dates
5. Project maturity date

### 1.2 Project Status
- **Coming soon**
- **Fundraising under way**
- **Fundraising closed**
- **Project ongoing** - project has started and money has been invested
  - This status can be manually activated by admin or triggered by first royalties payment
  - Capital will be on hold in Huudi's bank account
  - If project is cancelled, all funds will be refunded to investors
  - If project is delayed, investors will be notified
- **Project finalized**

### 1.3 Core Business Rules

#### Royalties and Subscription Relationship
- **Royalties period always after subscription period ends** - therefore, you never receive capital after royalties have been paid
- **Confirmed**: royalties period will always be after subscription period ends. Therefore, you never receive capital after royalties have been paid.

#### Early Redemption
- **Early redemption is always full notional**, not partial
- After investor decides to early redeem, Huudi has 6 months to repay

#### Project Termination
- **Projects may not reach maturity** (bankruptcy, early call from Huudi, etc.) - **POSSIBLE**
- Admin can decide which projects to display
- For matured projects, admin decides whether to display
- **Note**: field called "estimated yield" will be called "Realized ROI" once project reaches "Project closed" stage

#### Over-subscription
- Over-subscription is allowed
- After subscription period ends, admin can choose to proceed with project or redeem everyone

#### Cashflow Table and Yield Relationship
- **Estimated yield is not a variable**
- Only one financial data source: the cashflow table
- Only way for admin to modify estimated yield is to modify cashflow table
- System automatically calculates corresponding estimated yield and displays in project
- Everything that is a variable (editable before project starts) can be modified after project starts

#### IBAN Handling
- IBAN cannot be uploaded creates many dependencies and complicates the equation
- System will create a tick box where user can say "I paid"
- Therefore payment will be considered done for the financial statement

#### Financial Statement
- Financial statement is fed by what really happens
- No provision needed - **Confirmed**

---

## 2. User Registration and Sign In

### 2.1 Responsible
- Responsible: Michelle, Viktor, skybao, ryan, Jane
- Platform: Backoffice
- Priority: High Priority
- Version: V1.0.0
- Design link: https://www.figma.com/design/4MOkzHG9bbGJsWoow6Iicw/Huudi?node-id=517-19044&t=PvyWcHGZiTMLvlLq-0
- Module: Sign up
- Feature: Finalize registration, Initial registration
- Create date: May 13, 2024

### 2.2 Registration Flow

#### Initial Registration
- Create account with full name (first name, last name), password, email
- Or use Google, Facebook credentials (requires completing basic information)
- Using Google, Facebook to create account requires completing basic registration information

#### Password Requirements
- Required field
- Minimum 8 characters
- At least 1 uppercase letter
- At least 1 lowercase letter
- At least 1 number
- At least 1 special character

#### Email Verification
- Email verification required after initial registration

#### Complete Registration
After first login following verification, user must provide:
- Physical address (using address checking plugin API)
- Phone number (optional)
- Check box to accept website terms and conditions and newsletter

### 2.3 Language Support
- Website available in English and French, users can switch languages

### 2.4 Access Control
- Users cannot invest in projects if not connected/hasn't created an account
- When attempting to invest, direct user to standard onboarding procedure
- Some limited information requires registered user access

#### Private Information Access Rules
- Regarding private information, it is the News tab
- Each project page has the following tabs:
  1. Overview, details of the project, about the project leader, Questions, News, and financial simulation
  2. Tabs accessible only to registered users: details of the project, about the project leader, Questions, and News

---

## 3. KYC Verification

### 3.1 Responsible
- Responsible: Michelle, skybao, ryan, Jane
- Platform: Website
- Status: Prod
- Priority: High Priority
- Version: V1.0.0
- Design link: https://www.figma.com/design/4MOkzHG9bbGJsWoow6Iicw/Huudi?node-id=0-1&t=PvyWcHGZiTMLvlLq-0
- Module: KYC

### 3.2 KYC Process Overview
- KYC is a user action when wanting to invest in a project
- Verification through manual review (ID card/passport + detection field only)
- Can start with manual verification initially, workload shouldn't be huge

### 3.3 KYC Questions and Answers

**Question 1**: KYC should be a user action and should not be in the admin dashboard, right? It should be in the user dashboard right?
- **Answer**: Confirm, KYC procedure is when a user wants to invest in a project.

**Question 2**: Which api for KYC should we use?
- **Answer**: No restriction, use the one you want

### 3.4 KYC Trigger Conditions
Users must complete KYC when investing:
1. User clicks investment button to enter KYC process
2. User can also complete KYC and upload ID + IBAN on Payment info page

### 3.5 KYC Status and Popup Logic

| User Status | Investment Button Behavior |
|------------|---------------------------|
| KYC process not handled, ID photo not uploaded, IBAN not entered | Popup requires uploading ID (required), entering IBAN (optional) |
| IBAN entered, ID photo not uploaded | Popup only requires ID upload, no IBAN entry field |
| ID upload completed, IBAN not entered/entered, and in Pending approval status | Direct to ID Pending approval popup, no IBAN entry field |
| ID upload completed and approved, IBAN not entered/entered | Direct to investment process, no need to re-enter KYC process |
| ID upload completed and rejected, IBAN not entered/entered | Direct to rejection popup, no IBAN entry field |

### 3.6 ID Photo Requirements
- At least 1 front photo
- Maximum 2 photos (front + back)

### 3.7 Bank Details Requirements
- Each user must add bank details to receive royalty payments
- Not a required field/step at beginning of KYC process
- User will receive notification to upload bank details when first royalty payment occurs

### 3.8 KYC Approval Process
1. User uploads ID/passport info, enters/doesn't enter IBAN, clicks submit
2. KYC enters pending approval status, requires Huudi admin approval
3. Backoffice Dashboard has KYC pending approval list
4. Backoffice User list has approval entry for KYC pending approval users
5. Admin can approve/reject

### 3.9 Operation Buttons by Status

| Status | Operation Buttons |
|--------|------------------|
| Pending approval | No operation buttons |
| Rejected | Can delete ID photo; after deleting one, Rejected status disappears and Submit button appears |
| Approved | Edit button only (displayed on Payment info page); if in approval status, clicking investment button goes directly to investment process |

### 3.10 Information Viewing and Editing
- Allow users to view their information (including ID) on Payment info page
- Users can edit information (e.g., expired ID)

---

## 4. Project Management

### 4.1 Responsible
- Responsible: Michelle, Viktor, skybao, ryan, Jane
- Platform: Backoffice
- Status: Prod
- Priority: High Priority
- Version: V1.0.0
- Design link: https://www.figma.com/design/4MOkzHG9bbGJsWoow6Iicw/Huudi?node-id=220-8329&t=sPJQOegXzZMK9cfK-0
- Module: All projects
- Feature: Active/Deactive projects, Add projects, Close projects, Modify projects

### 4.2 External Reference Links
| Link Type | URL |
|-----------|-----|
| Backoffice mind map | https://nlluck6bjh.feishu.cn/docx/NqZjdOwvloYT21x62YwcpKdunxf |
| Investment/Royalties/Redemptions date rules | https://docs.google.com/spreadsheets/d/1VsS9etT29UJ9JqkgcXG5G-PvGAxK7EF_KjnwU44JwcI |
| All public information | https://docs.google.com/document/d/14xd7uJI5JntY9ZcqUwE52yQ8h7v9PUzubvpjgi6Qeoo |
| Projects information reference | https://www.wiseed.com/projet/71770273/ |
| Figma Design | https://www.figma.com/design/4MOkzHG9bbGJsWoow6Iicw/Huudi |

### 4.3 Project Type Management

#### Responsible
- Responsible: Michelle, Viktor, skybao, ryan, Jane
- Platform: Backoffice
- Status: Prod
- Priority: Average Priority
- Version: V1.0.0
- Module: Project
- Feature: Project type
- Create date: May 16, 2024

#### Default Four Project Types
1. Mining
2. Precious metals
3. Energy
4. Trading

#### Fields for Each Project Type
- **Annual rate of return**:
  - Mining/Precious metals: 25-30%
  - Energy: 20-30%
  - Trading: 25-30%
- **Risk**:
  - Precious metals/Mining: Medium
  - Energy: Low
  - Trading: Medium/High
- **Time Horizon**: 12-24 months for all types

Admin can edit parameters for each type

### 4.4 Creating New Project

#### Important Note
- **No need to upload cashflow file when creating new project**, cashflow file data comes from basesheet

#### Basic Information
- Funding objective
- Scoring: selection box, default values A, B, C
- Project leader
- Investment type: royalties or lending
- Project picture(s) (multiple pictures)
- Name
- Start date: must be >= current date
- Project live date: > Start date
- Subscription end date: > Project live date
- Early redemption Start date: > Subscription end date
- Early redemption End date: > Early redemption Start date
- Royalties payment dates:
  - First payment > Subscription end date
  - Second payment > First payment
  - And so on...
- Project Maturity date: >= Last royalties payment AND > Early redemption End date
- Short Description
- Long Description
- Type: obtained from Project type page (MINING, PRECIOUS METALS, ENERGY, TRADING)
- Location
- Main focus points

#### Overview and Details
- Overview: description + picture(s) (optional)
- Details of project: multiple pictures + text (all optional, but if all fields are empty there's nothing to publish)

#### Investor Information
- Investment conditions (upload by template)
- Use one input table for both estimation and realized data

#### Management Information
- Active/Deactive
- There's a project initiation/start phase between subscription end date and early redemption start date, entered by admin

#### Project News
- Up to 4 pictures and 4 text fields
- Every field is optional (but if all fields are empty there's nothing to publish)
- Can be added at any stage except project closure
- Admin can choose which news are visible

### 4.5 Modifying Projects

| Project Status | Editable Information |
|---------------|---------------------|
| Project not started | All project information |
| Coming soon (start date arrived) | Start date cannot be edited, others can be edited |
| Fundraising under way | Start date and Project live date cannot be edited |
| Fundraising closed | Start date, Project live date, Subscription end date cannot be edited |
| Project ongoing | Project information, Base sheet; admin cannot modify dates of different project statuses |
| Project finalized | All information cannot be edited |

### 4.6 Project Q&A
- Admin can view question list for each project
- Can respond to each question
- Can delete all questions and any responses at any time
- Reply time display rule: xx mins ago, xx days ago; over 1 month shows reply date directly

### 4.7 Project Termination Handling

#### Project Cancellation
- If admin decides to cancel project, backoffice generates orders to redeem investors' capital
- Order status is payable, waiting for admin to return money to investors

#### Project Delay
- If admin decides to delay project start, admin can set start time in backoffice
- Investors will receive corresponding reminders
- Before delayed project starts, can be changed to cancel project/start project

### 4.8 Content After Project Creation
1. Added base sheet table histories (list of excel files)

### 4.9 New Content When Project is Ongoing
1. Add new Transaction history entry on project details page
2. Can view all Subscription/Royalties/Redemptions history of this project
3. User can filter transaction history of each project by different transaction types

### 4.10 Project List
- Sorted in descending order of creation time
- Can filter by project type
- Can search by project name

---

## 5. Investment Flow

### 5.1 Responsible
- Responsible: Michelle, Viktor, ryan, skybao, Jane
- Platform: Website
- Status: Prod
- Priority: High Priority
- Version: V1.0.0
- Design link: https://www.figma.com/design/4MOkzHG9bbGJsWoow6Iicw/Huudi?node-id=147-7422&t=Hw0cUfNWDE0gx0qn-0
- Module: All projects
- Feature: Project list, Project type
- Create date: May 15, 2024

### 5.2 External Reference Links
| Link Type | URL |
|-----------|-----|
| Huudi website link | https://huudi-tz.com |
| All public information | https://docs.google.com/document/d/14xd7uJI5JntY9ZcqUwE52yQ8h7v9PUzubvpjgi6Qeoo |
| Wiseed reference project | https://www.wiseed.com/projet/71770273/ |
| Miimosa reference project | https://miimosa.com/projects/la-quercynoise-groupement-majeur-du-sud-ouest-de-la-france |
| Investment flow chart | https://nlluck6bjh.feishu.cn/wiki/ERYZwpMMei6gAnkWynNclioUnCb |
| Redemption flow chart | https://nlluck6bjh.feishu.cn/wiki/K6hNw10c1iVAxekZeFpcNalnnwh |

### 5.3 Project List Page
Display all projects available for crowdfunding, including:
- Number of investors
- Target financing amount
- Amount raised
- Short description
- Remaining time for financing
- Estimated annual yield
- Project tenor

**Project tenor calculation rules**:
- Project tenor = Project maturity date - Project start date
- Always round up to next month (e.g., 23 months 1 day = 24 months)

**Project type filtering**:
- Buttons to filter project types (mining, precious metals, energy, trading)
- If project type is empty (not enough projects), still show on website, displaying no projects data page

### 5.4 Project Detail Page

#### Displayed Information
- Target amount to be raised
- Annual estimated yield
- Project tenor
- Investment type (royalties or lending)
- Project score
- Project description
- Project main focus points

#### Follow Feature
- User can click follow button (or star bookmark button) to follow projects
- Followed projects will appear in user interface

### 5.5 Project Page Tabs
1. **Overview** - Accessible to everyone
2. **Details of the project** - Registered users only
3. **About the project leader** - Registered users only
4. **Questions** - Registered users only
5. **News** - Registered users only
6. **Financial simulation**

### 5.6 Investment Button
- Only displayed during period from "Project live date" to "Subscription end date"

### 5.7 Investment Rules
- Minimum investment: 100€ per project
- Users can invest 121€ (doesn't need to be multiple of 100)
- Personal investment only

### 5.8 Financial Simulator
- Simple calculator: input investment amount, show payment schedule
- Calculation formula: **Investment amount × Annual Yield × Project Tenor (in years)**

**Example**:
- Investment amount = 1300€
- Annual Yield = 11%
- Tenor = 24 months
- Royalties = 1300 × 11% × 2 = 286€

### 5.9 Other Projects Recommendation
- "Other Projects" tab at bottom of each project page
- Randomly recommend three other projects

### 5.10 Investment User Interface
- Users can complete KYC and upload ID + IBAN on their user Profil page in documents tab

---

## 6. Backoffice Management

### 6.1 Backoffice Sign In

#### Responsible
- Responsible: Michelle, Viktor, skybao, ryan, Jane
- Platform: Backoffice, H5
- Status: Prod
- Priority: High Priority
- Version: V1.0.0
- Module: Sign in

#### Function Description
- Only admin can log in to Backoffice
- System configured with one super admin account by default

### 6.2 Admin Account Management

#### Responsible
- Platform: Backoffice
- Status: Prod
- Priority: Average Priority
- Version: V1.0.0
- Module: Settings
- Feature: Account
- Create date: May 16, 2024

#### Function Description
- Admin account management page exists
- Super admin account not displayed on this page
- Can add new admin
- Can activate/deactivate admin accounts

### 6.3 User Management

#### Responsible
- Responsible: Viktor, Michelle, skybao, ryan
- Platform: Backoffice
- Status: Prod
- Priority: Average Priority
- Version: V1.0.0
- Module: Users
- Feature: KYC approval
- Create date: May 17, 2024

#### Function Overview
1. Display list of all registered users
2. List mainly displays following fields:
   - Email
   - First name, last name
   - Address
   - Creation date
   - KYC status
3. If KYC is pending approval, corresponding user has approval entry

### 6.4 Dashboard Metrics

#### Responsible
- Responsible: Michelle, skybao, ryan, Jane
- Platform: Backoffice
- Status: Prod
- Priority: Average Priority
- Version: V1.0.0
- Module: Dashboard
- Feature: review the metrics
- Create date: May 8, 2024

#### Metrics to Review

1. **Total investment**

2. **Total number of investors**

3. **Amount invested per project**

4. **Funded projects**
   - Projects with fundraising closed and project started
   - Completed projects not displayed in list are also counted

5. **Global payment schedule/cash flow** - filterable by project, user
   - A cash flow calendar showing all scheduled cash flows:
     - Initial investment
     - Royalties payment according to each project's schedule
     - Capital reimbursement at project maturity date
   - And following markers:
     - Marker when user can exercise redemption right
     - Marker when project will end and project leader will have to reimburse initial investment
     - Marker when funding collection period ends

6. **Show list of KYC pending approval**

7. **Show list of projects to be launched**

8. **Possibility to see investment of each user**

#### Royalties Calculation Rules (Important Algorithm)

**Realized Royalties Calculation Algorithm**:
- Realized royalties for each project = sum of royalties generated by all investors in each project during corresponding period
- Including paid royalties and planned paid royalties (unsigned/no bank information)

**Unsigned Contract Royalties Handling**:
- For royalties without signed contract, we will not generate a royalties transaction order
- After contract is signed, we will generate a royalty transaction order for all previously unpaid royalties

**Missing Information Royalties Handling**:
- If royalties are not paid because of missing info (contract, bank info, etc...), this should not change the calculation for admin or user

**Admin Dashboard Realized Royalties**:
- On admin dashboard, realized royalties is the total past royalties paid and to be paid (including pending payment etc...)

**Pending Payment Handling**:
- Pending payment should not impact the global table

**Royalties and Capital Definition per Project**:
- Each project's royalties represents the Final royalties of all investors for each period of each project
- Each project's capital also represents the capital paid by all investors for each period of each project

#### Refund Methods
1. Wait for maturity date
2. Exercise refund right early if project royalties don't match estimation

### 6.5 Investor Financial Information

#### Responsible
- Responsible: Michelle, skybao, ryan, Jane
- Platform: Backoffice
- Status: Prod
- Priority: Average Priority
- Version: V1.0.0
- Module: Users
- Feature: Financial info
- Create date: June 13, 2024

#### Function Overview
1. Add Financial Block on Investor details page
2. Display following three values on Financial Block:
   - Amount invested €
   - Royalties received €
   - Capital reimbursed €

#### Behavior When Value is 0
- As long as value is 0, corresponding field has no > button
- Cannot view transaction source of corresponding value

#### Behavior When Value is Not 0
- As long as value is not 0, corresponding field has > button
- Can view transaction source of corresponding value

#### Button Navigation Logic
1. **Click Amount invested > button**:
   - Navigate to corresponding transaction history page
   - Page default filters: corresponding investor
   - Transaction type is Investment
   - Transaction status is paid

2. **Click Royalties received > button**:
   - Navigate to corresponding transaction history page
   - Page default filters: corresponding investor
   - Transaction type is Royalty
   - Transaction status is paid

3. **Click Capital reimbursed > button**:
   - Navigate to corresponding transaction history page
   - Page default filters: corresponding investor
   - Transaction type is Redemption (early) and Redemption
   - Transaction status is paid

---

## 7. Finance and Cashflow

### 7.1 Cashflow Table

#### Management Rules
- Only manage one input table for both estimation and realized data
- When updating table, past is realized, future is estimation
- After maturity date, everything is realized

#### Excel Template Usage
- Will create an Excel template for cashflow table
- Admin fills fixed template and uploads to website
- Backoffice will retrieve relevant information
- When re-uploading template to update royalties and future estimations, it will erase previous one
- No history of previous table is kept

#### Update Rules
- Cashflow table is here from the beginning
- On each royalties payment you are going to modify it
- Cashflow table only updated via Excel

#### Usage at Project Creation
- Same template will also be used on project creation to fill all necessary information (such as dates)
- When you have uploaded that template, you will be able to modify those dates directly via the back office
- Cashflow table only updated via Excel

### 7.2 Cashflow Table and Estimated Yield Relationship
- Estimated yield is not a variable
- Only one financial data source: the cashflow table
- Only way for admin to modify estimated yield is to modify cashflow table
- System automatically calculates corresponding estimated yield and displays in project

### 7.3 Royalties Calculation Rules (Core Algorithm)

#### Calculation Formula
**Royalties = Total royalties paid × Invested amount / Amount required to start project (≠ Total amount invested)**

#### Example Explanation
If a project generates 100k€ revenue:
- 5 users invested 10k
- 3 users invested 5k
- 1 user invested 20k

**Calculation method**:
- When admin updates cashflow table with realized amount, royalties + ROI of each user is automatically computed
- Calculation = Total royalties paid × Invested amount / Amount required to start project

### 7.4 Royalties Update
- Admin updates cashflow table of each project monthly
- This will feed into financial statement of each user
- Modify estimated ROI

### 7.5 Actual Royalties vs Estimation
- Royalties payment may differ from estimation
- Admin updates cashflow table monthly
- Each user's royalties + ROI automatically calculated

### 7.6 Project Termination Handling
- When admin decides to cancel project, backoffice generates orders to redeem investors' capital
- Order status is payable, waiting for admin to return money to investors
- When project is delayed, admin can set start time in backoffice, investors receive corresponding reminders
- Before delayed project starts, can be changed to cancel project/start project

---

## 8. Static Pages

### 8.1 How it Works

#### Responsible
- Responsible: Michelle, Viktor, Jane
- Platform: H5
- Status: Prod
- Priority: Average Priority
- Version: V1.0.0
- Design link: https://www.figma.com/design/4MOkzHG9bbGJsWoow6Iicw/Huudi?node-id=1167-27482&t=G7SEhTWaKamQrSgV-0
- Module: How it works
- Feature: How it works

#### Function Overview
- Static page, reference: https://huudi-tz.com/en/how-it-works/
- Available in EN and FR

#### Page Interactions
1. Click "DISCOVER OUR PROJECTS" button to jump to Projects page
2. Click "JOIN US" button to go to How it works page
3. **"WHAT ARE THE ADVANTAGES ?" block**:
   - √ next to each paragraph is gray by default
   - Slide page to any paragraph, √ at corresponding position turns red and highlights
4. Click "Become a contributor/Discover our projects" button to jump to Project page
5. Click "CONTACT US" button to jump to CONTACT US page
6. Click "VIEW FAQ" button to jump to FAQ page

### 8.2 Our CSR Commitment and Policy

#### Responsible
- Responsible: Viktor, Michelle, Jane
- Platform: H5
- Status: Prod
- Priority: Average Priority
- Version: V1.0.0
- Design link: https://www.figma.com/design/4MOkzHG9bbGJsWoow6Iicw/Huudi?node-id=1167-27482&t=G7SEhTWaKamQrSgV-0
- Module: know US
- Feature: OUR COMMITMENTS
- Create date: May 31, 2024

#### Function Overview
- Static page, reference: https://huudi-tz.com/notre-engagement-et-politique-rse/
- Available in EN and FR

### 8.3 News

#### Responsible
- Responsible: Michelle, skybao, ryan, Jane
- Platform: Website
- Status: Prod
- Priority: Low Priority
- Version: V1.0.0
- Module: NEWS
- Feature: Read more, View NEWS
- Create date: May 16, 2024

#### Function Description
1. All news created and activated from backoffice displayed in list form under NEWS tab on website
2. Can click "Read more" button to view news detail page
3. News not activated in backoffice not displayed on website NEWS tab
4. Contents of NEWS tab visible regardless of user login status

---

## 9. Q&A and Feedback

### 9.1 Questions Page Access Control
- Registered users only

### 9.2 Question Posting Rules
1. Each successfully logged-in user can post comments on project Questions page
2. All users can only respond to questions posted by others, cannot respond to own questions
3. Cannot respond to others' responses (reply level is only one level)

### 9.3 Deletion Rules
- Can only delete own questions/comments
- Cannot delete others' questions/comments

### 9.4 Admin Replies
- If Huudi admin reply, admin name "Huudi" displayed in red font

### 9.5 Time Display Rule
- xx mins ago
- xx days ago
- Over 1 month: display reply date directly

---

## 10. Algorithm Rules

### 10.1 Project Tenor Calculation Algorithm
```
Project tenor = Project maturity date - Project start date
Rule: Always round up to next month
Example: 23 months 1 day = 24 months
```

### 10.2 Royalties Calculation Algorithm
```
Royalties = Total royalties paid × Invested amount / Amount required to start project

Note: Amount required to start project ≠ Total amount invested
```

**Example**:
- Project generates 100k€ revenue
- 5 users invested 10k
- 3 users invested 5k
- 1 user invested 20k
- When admin updates cashflow table with realized amount, each user's royalties + ROI is automatically calculated

### 10.3 Financial Simulator Calculation Algorithm
```
Royalties = Investment amount × Annual Yield × Project tenor (in years)

Example:
Investment amount = 1300€
Annual Yield = 11%
Tenor = 24 months
Royalties = 1300 × 11% × 2 = 286€
```

### 10.4 Realized Royalties Calculation Algorithm (Admin Dashboard)
```
Realized royalties for each project = sum of royalties generated by all investors in each project during corresponding period
Including:
- Paid royalties
- Planned paid royalties (unsigned/no bank information)
```

### 10.5 Unsigned Contract Royalties Handling Rules
```
For royalties without signed contract:
1. No royalties transaction order generated
2. After contract signed, generate royalty transaction order for all previously unpaid royalties
```

### 10.6 Missing Information Royalties Handling Rules
```
If royalties not paid due to missing information (contract, bank info, etc.):
- Should not change calculation for admin or user
```

### 10.7 Admin Dashboard Realized Royalties
```
Realized royalties = Past paid royalties + Pending royalties total
Including: pending payment etc.
```

### 10.8 Estimated Yield Calculation
```
Estimated yield is not a variable
Only data source: cashflow table
Calculation: Modify cashflow table → System automatically calculates corresponding estimated yield → Display in project
```

### 10.9 Cashflow Table Update Rules
```
1. Only manage one input table (estimation + realized)
2. When updating: Past = realized, Future = estimation
3. After maturity date: All data = realized
4. Re-upload erases previous table
5. No history kept
```

---

## 11. User Interface Experience

### 11.1 Input Field Validation

#### Responsible
- Platform: Website
- Status: Prod
- Version: V1.0.0
- Demand Source: Feedback_website_V1.docx
- Estimated dev time: 1 Day
- Select: Confirm to do in V1.0.0

#### Function Description
- For all input boxes on website
- Validate field when pressing Tab after filling

### 11.2 Project Picture Display
- Gallery format with one main picture
- Can switch like typical carousel
- Images also in project description

### 11.3 Estimated Yield Field Name Change
- "Estimated yield" field will be called "Realized ROI" once project reaches "Project closed" stage

---

## 12. Business Questions and Discussion Records

### 12.1 News Feature Discussion
**Discussion content**:
- Can't do news like Wiseed as it is CMS, it's way more work
- We can do it in a second stage of the project

**Response**:
- Since we can put a project description, and we can modify the description at all moment, why couldn't we create another tab, which is called news, and it's exactly the same thing as the description?

**Follow-up requirements**:
- Need clear definition of such so-called NEWS: format of the text? pictures? layout of those text & picture?
- Layout is fixed, only one layout possible

**Final solution**:
- Project news: up to 4 pictures and 4 text fields
- Every field is optional
- Can be added at any stage except project closure
- Admin can choose which news are visible

### 12.2 Complete Project Information Request
**Request**:
- Can you send us 2-3 complete projects with all information so that we can understand better

**Response**:
- Confirm, he will send us a link to the huudi website where some projects are already disclosed

**Additional comment**:
- We don't need public website link, we need ALL project elements
- What public sees we can already check from the website
- We need all inside rules

### 12.3 Picture Handling Discussion
**Question**:
- Is there only one picture? gallery? is there a limit to the amount of pictures?

**Answer**:
- It is a gallery of picture, with one main picture, which can be switched like a typical carousel
- In the project description, there will also be images

### 12.4 IBAN Cannot Be Uploaded Handling
**Problem**:
- The fact that IBAN can NOT be uploaded is creating a lot of dependencies and complexifying the equation

**Solution**:
- Create a tick box where user can say "I paid"
- Therefore payment will be considered done for the financial statement

### 12.5 Financial Statement Principle
**Principle**:
- Financial statement is fed by what really happens
- Don't want any provision
- **Confirmed**

### 12.6 Royalties Payment Difference Handling
**Question**:
- Payment of royalties can and will be different from the estimation. What happens to the cashflow and ROI estimation?

**Answer**:
- Each month admin will update cashflow table of each project
- This will feed into financial statement of each user
- Modify estimated ROI

---

## 13. External Reference Links

| Link Type | URL |
|-----------|-----|
| Figma Design | https://www.figma.com/design/4MOkzHG9bbGJsWoow6Iicw/Huudi |
| Backoffice mind map | https://nlluck6bjh.feishu.cn/docx/NqZjdOwvloYT21x62YwcpKdunxf |
| Investment/Royalties/Redemptions date rules | https://docs.google.com/spreadsheets/d/1VsS9etT29UJ9JqkgcXG5G-PvGAxK7EF_KjnwU44JwcI |
| Cashflow table example | https://docs.google.com/spreadsheets/d/1KuShm9UgkUgeF0KGqL2kNP0sK_lbGw1TgEkExZI5erc |
| Public information | https://docs.google.com/document/d/14xd7uJI5JntY9ZcqUwE52yQ8h7v9PUzubvpjgi6Qeoo |
| Investment flow chart | https://nlluck6bjh.feishu.cn/wiki/ERYZwpMMei6gAnkWynNclioUnCb |
| Sign up flow chart | https://nlluck6bjh.feishu.cn/wiki/Kq7ZwQTz2iBTxDkfLTjcqWCCnSh |
| Redemption flow chart | https://nlluck6bjh.feishu.cn/wiki/K6hNw10c1iVAxekZeFpcNalnnwh |
| Wiseed reference project | https://www.wiseed.com/projet/71770273/ |
| Miimosa reference project | https://miimosa.com/projects/la-quercynoise-groupement-majeur-du-sud-ouest-de-la-france |
| Huudi website | https://huudi-tz.com |
| How it works page | https://huudi-tz.com/en/how-it-works/ |
| CSR page | https://huudi-tz.com/notre-engagement-et-politique-rse/ |

---

## 14. Project Responsible Persons

| Responsible |
|-------------|
| Viktor |
| Michelle |
| skybao |
| ryan |
| Jane |

---

*Document Generated: 2024*
*Version: V1.0.0*
