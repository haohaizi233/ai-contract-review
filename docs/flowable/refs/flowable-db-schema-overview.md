# Flowable Database Schema | Flowable Enterprise Documentation

The Flowable engines tables follow a common naming pattern\. Each pattern for each engine is described in detail below\.

The database table names associated with the Flowable open source code base start with **ACT\_**\. Database tables specific to Flowable Work or Engage start with the **FLW\_** prefix\.

## Common Secondary Identifiers[​](#common-secondary-identifiers "Direct link to Common Secondary Identifiers")

The second part of the table name \(after the first **'\_'**\) is a two character identification specifying the specific use case of the table\.

- **GE\_**: GE stands for general data, which is used for various use cases\.

- **RU\_**: RU stands for runtime\. These are the runtime tables that contain the runtime data of processes, cases, etc\. that are not yet finished\. Flowable only stores the runtime data during execution and removes the records after the instances ends\. This keeps the runtime tables small and fast\.

- **HI\_**: HI stands for history\. These are the tables that contain historical data\. When data is removed from the runtime tables, the history tables still contain all the information for these finished instances\.

- **RE\_**: RE stands for repository\. Tables with this prefix contain static information around models and definitions\.

## Common Suffixes[​](#common-suffixes "Direct link to Common Suffixes")

Flowable engines typically create a *deployment* which results in one or more *definitions*\. This is done by passing models \(e\.g\., a process model, a case model, etc\.\) to the engine API\. The engine parses the content of the model\. One deployment can have multiple models\. As such a *deployment* is a logical sort of container for related definitions\.

If the deployment is successful, the following entries are created in the following tables:

- **\<prefix\>\_DEPLOYMENT**: One entry to indicate the successful deployment\. All definitions that are created as part of the *deployment* reference this entry\.

- **\<prefix\>\_DEPLOYMENT\_RESOURCES**: Stores the content of the models related to a deployment\. When the engine reboots and a specific definition is needed, this table is queried to rebuild the runtime behaviour based on the model\.

- **\<prefix\>\_DEF\(INITION\)**: One deployment can have multiple *definitions*\. Typically there is definition for each model in the deployment \(e\.g\., a process definition, case definition, form definition, etc\.\)\. The table contains all the definitions that are known to the engine and from which new instances of the definition can be started\.

## Common Database Tables[​](#common-database-tables "Direct link to Common Database Tables")

The original database tables \(like those for BPMN and IDM\) use a Flowable schema and scripts for version management\. The newer engines and tables use Liquibase for table versioning\. There are two Liquibase tables related to database schema management:

- **\<prefix\>\_DATABASECHANGELOG**: This table is used by Liquibase to track which changesets have been run\.

- **\<prefix\>\_DATABASECHANGELOGLOCK**: This table is used by Liquibase to ensure only one instance of Liquibase is running at a time\.

Note: [Liquibase](https://www.liquibase.org/) is a Java based framework for tracking database schema changes\.

## App Engine Database Tables[​](#app-engine-database-tables "Direct link to App Engine Database Tables")

The app engine is responsible for storing and deploying *app definitions*\.

The database tables for the App Engine are prefixed with **ACT\_APP\_**\.

- **ACT\_APP\_DEPLOYMENT**: When an app model is deployed via the app engine, a record is stored to indicate this deployment\. The actual content of the deployment is referenced and stored in the *ACT\_APP\_DEPLOYMENT\_RESOURCE* table\.

- **ACT\_APP\_DEPLOYMENT\_RESOURCE**: This table contains the actual resource \(stored as bytes\) that make up an app deployment\. When the engine needs the actual model, the resource is fetched from this table\.

- **ACT\_APP\_APPDEF**: The app model results in an application definition\. This definition, like process/case/etc\. is a representation of an app model that was successfully deployed to the app engine\.

## CMMN Engine Database Tables[​](#cmmn-engine-database-tables "Direct link to CMMN Engine Database Tables")

The database names of the Flowable CMMN Engine all start with **ACT\_CMMN\_**\. The second part is a two character table name specifying the use case of the table\. This use case roughly matches the service APIs\.

- **ACT\_CMMN\_**: Tables without an additional prefix contain static information such as case definitions and case deployment and resources\.

- **ACT\_CMMN\_HI\_**: HI stands for history\. These are the tables that contain historic data, such as past case instances, plan items, and so on\.

    -

**ACT\_CMMN\_HI\_CASE\_INST**: This table contains an entry for each case instance that was started by the CMMN engine\.



    -

**ACT\_CMMN\_HI\_MIL\_INST**: This table contains an entry for each milestone that was reached in a case instance\.



    -

**ACT\_CMMN\_HI\_PLAN\_ITEM\_INST**: This table contains an entry for each plan item instance that was created as part of the case instance execution\.



- **ACT\_CMMN\_RU\_**: RU stands for runtime\. These are the runtime tables that contain the runtime data of case instances, plan items, and so on\. Flowable only stores the runtime data during case instance execution and removes the records when a case instance ends\. This keeps the runtime tables small and fast to query\.

    -

**ACT\_CMMN\_RU\_CASE\_INST**: This table contains an entry for each case instance that was started and is not yet finished\.



    -

**ACT\_CMMN\_RU\_MIL\_INST**: This table contains an entry for each milestone that was reached as part of a running case instance\.



    -

**ACT\_CMMN\_RU\_PLAN\_ITEM\_INST**: A case instance execution consists of multiple instance of the plan items defined in the case definition\. This table contains an entry for each instance that gets created during case instance execution\.



    -

**ACT\_CMMN\_RU\_SENTRY\_PART\_INST**: Plan item instances can have sentries that guard state transitions\. Such a sentry can contain multiple parts that need to be satisfied before the state change happens\. This table stores any part of such a sentry that is satisfied\.



## DMN Database Tables[​](#dmn-database-tables "Direct link to DMN Database Tables")

The database names of Flowable DMN all start with **ACT\_DMN\_**\.

- **ACT\_DMN\_DECISION\_TABLE**: This table contains the metadata of the deployed decision tables and corresponds with a *definition* from the other engines\.

- **ACT\_DMN\_HI\_DECISION\_EXECUTION**: This table contains audit information about DMN decision table executions\.

## Process Engine Database Tables[​](#process-engine-database-tables "Direct link to Process Engine Database Tables")

The tables of the process engine start with **ACT\_**\. There is additional further prefix \(like the other engines: **CMMN** or **DMN**\) as this was the first engine that was built\. Also note that because of this, some tables are reused by other engines\.

- **ACT\_RU\_**: Contains the runtime data for process instance and the related data\. Once a process instance is finished, all runtime data is deleted from these tables\.

    -

**ACT\_RU\_EXECUTION**: Stores the process instances and pointers \(called *executions*\) to the process instance current state\.



    -

**ACT\_RU\_ACTINST**: Each activity in the process instance has a row in this table to indicate the current state of the activity\.



    -

**ACT\_RU\_\<TYPE\>\_JOB**: The Flowable engines use the *jobs* tables to implement asynchronous logic, timers or history processing\. These tables store the data needed for each jobs\.



    -

**ACT\_RU\_ENTITYLINK**: This table stores information about parent-child relations for instances\. For example, if a process instance starts a child case instance this relationship is stored in this table\. This enables easy querying of relationships\.



    -

**ACT\_RU\_EVENT\_SUBSCR**: When a process definition uses events \(signal/message/etc\. or start/intermediate/boundary\), the engine stores a reference to this table it in this table\. This simplifies querying for which instances are waiting for a certain type of event\.



    -

**ACT\_RU\_IDENTITYLINK**: This table stores data about user or groups and their role with regards to the \(process/case/etc\.\) instances\. This table is also used by the other engines needing identity links\.



    -

**ACT\_RU\_TASK**: This table contains an entry for each unfinished user task of a running instance\. This table is then used when querying for tasks lists of users\. This table is also used by the CMMN engine\.



    -

**ACT\_RU\_VARIABLES**: This table stores variables related to an instance\. This table is also used by the CMMN engine\.



- **ACT\_HI\_**: These tables contain historical data for both running and completed instances\. The names of these tables follow the names of their runtime counterpart\.

## Action Engine Database Tables[​](#action-engine-database-tables "Direct link to Action Engine Database Tables")

The action engine is responsible for storing *action definitions* and creating and storing *action instances*\. *Actions* are triggerable items that typically are created as part of process or case instances and shown in a user interface\. When triggered, they execute custom logic associated with its *action definition*\.

The database tables for the Action Engine are prefixed with **FLW\_ACTION\_**\. Besides the typical deployment and definition tables, there are the following tables:

- **FLW\_ACTION\_INSTANCE**: Similar to creating a process/case instance from a process/case definition, an *action instance* is created for a given definition\. The entry for it is stored in this table\.

- **FLW\_ACTION\_DEF\_LINK**: Action definitions can be linked to other entities, for example to only be visible within a certain context\. Such a relationship is stored in this table\.

- **FLW\_ACTION\_LINK**: Similarly, an action instance can have links too and these relationships are stored here\.

- **FLW\_HI\_ACTION\_INSTANCE**: This table contains historical information about completed action instances\.

- **FLW\_HI\_ACTION\_LINK**: This table contains history information about relationships that are no longer active\.

## Audit Database Tables[​](#audit-database-tables "Direct link to Audit Database Tables")

The audit engine allows to create and query an audit stream of business events\. Such events are called *audit instances* and are typically created during the execution of process or case instances\.

The database tables for the Audit Engine are prefixed with **FLW\_AUDIT\_** or **FLW\_AU**\. Besides the typical deployment and definition tables, there are the following tables:

- **FLW\_AUDIT\_INSTANCE**: Each entry in this table represents one business event\.

## Content Engine Database Tables[​](#content-engine-database-tables "Direct link to Content Engine Database Tables")

The content engine is used to store and query content\. Content can be documents, images, etc\.

The database tables for the Content Engine are prefixed with **FLW\_CO\_**\. Besides the typical deployment and definition tables, there are the following tables:

- **FLW\_CO\_CONTENT\_ITEM**: Each item of content has an entry in this table\.

- **FLW\_CO\_CONTENT\_OBJECT**: Contains the actual bytes of the content item assuming the filesystem implementation of content is not used\.

- **FLW\_CO\_CONTENT\_METADATA**: A content item can have associated metadata \(e\.g\. author, creation data, etc\.\)\. Such metadata is stored in this table\.

- **FLW\_CO\_CONTENT\_RENDITION\_ITEM**: Some content can be represented in different ways, which is called a *rendition*\. For example a Word document might get a PDF rendition or a thumbnail PNG rendition\. This table stores such rendition information for content items\.

## Data Object Engine Database Tables[​](#data-object-engine-database-tables "Direct link to Data Object Engine Database Tables")

The object engine is used to store and query objects\. The tables are used in conjunction with Master Data processing\.

The database tables for the Data Object Engine are prefixed with **FLW\_DO\_** or **FLW\_DATAOBJECT\_**\.

## Engage Engine Database Tables[​](#engage-engine-database-tables "Direct link to Engage Engine Database Tables")

The Engage Engine enables conversations and messages\.

The database tables for the Engage Engine are prefixed with **FLW\_ENGAGE\_**\. There is currently one table that does not follow the pattern, **FLW\_CONV**, which in the future will use the common prefix\.

Besides the typical deployment and definition tables, the tables in Engage have names like CONVERSATION, MESSAGE/CONVERSATION\_STATUS, etc\. which are self-explanatory\.

## Form Engine Database Tables[​](#form-engine-database-tables "Direct link to Form Engine Database Tables")

The form engine stores data around form models and instances of those forms\.

The database tables for the Form Engine are prefixed with **FLW\_FO\_**\. The tables here have the typical deployment, definition and instance names\.

## Identity Engine Database Tables[​](#identity-engine-database-tables "Direct link to Identity Engine Database Tables")

The IDM \(identity management engine\) stores data about users and groups\.

The database tables for the Identity Engine are prefixed with **FLW\_ID\_**\.

- **FLW\_ID\_USER\_DEFINITIONS**: Each Flowable user has a *user definition*\. This definition defines the role\(s\) of the user, which features are enabled for the user, how the user gets created, etc\.

- **FLW\_ID\_USER**: This table contains an entry for each user defined in the system\.

- **FLW\_ID\_GROUP**: This table contains an entry for each group defined in the system\.

- **FLW\_ID\_MEMBERSHIP**: This table maps the relationship between users and groups\. That is, which users are part of which groups\.

- **FLW\_ID\_TOKEN**: When a user successfully logs in, a token gets stored as part of the session-data\. Each server in a cluster can query this database table to coordinate user sessions on each server\.

- **FLW\_IDENTITY\_INFO\_**: This table stores unstructured data about users that is not stored in the *FLW\_ID\_USER* table\.

- **FLW\_ID\_USER\_ACCOUNT\_DEFINITIONS**: This table stores information about third party user accounts for other systems\.

- **FLW\_ID\_USER\_ACCOUNT**: This table stores a specific instance of a user account definition\. It contains the specific user account detail for a specified account in the third party system\.

- **FLW\_ID\_PRIV**: Not currently used\.

- **FLW\_ID\_PRIV\_MAPPING**: Not currently used\.

## Policy Engine Database Tables[​](#policy-engine-database-tables "Direct link to Policy Engine Database Tables")

The policy engine is responsible for enforcing user access rights to objects\. The database tables are prefixed with **FLW\_POLICY\_**\.

## Service Engine Database Tables[​](#service-engine-database-tables "Direct link to Service Engine Database Tables")

The service engine is used to store, query and execute service definitions\. A service definition describes what operations a service has, what parameters are expected and how the service is invoked\. With these definitions process or case modelers can use the services without having to know the implementation details \(e\.g\., a REST service, an expression, etc\.\)\. The database tables are prefixed with **FLW\_SE\_**\.

## Template Engine Database Tables[​](#template-engine-database-tables "Direct link to Template Engine Database Tables")

The template engine is responsible for generating content out of a template \(e\.g\., a Freemarker template, a Word template, etc\.\)

The database tables for the Template Engine are prefixed with **FLW\_TEMPL\_**\. Besides the typical deployment and definition tables, there is the following table:

- **FLW\_TEMPL\_VAR\_DEFINITION**: Template definitions can have variations; for example, a Word template in different languages\. Each template variation has some associated logic defining when the specific variation is used\. The definition of that logic is stored in this table\.

## Flowable Control Database Tables[​](#flowable-control-database-tables "Direct link to Flowable Control Database Tables")

The table associated with Flowable Control have the prefix **FLW\_CTRL\_**\.

- **FLW\_CTRL\_CLUSTER\_CONFIG**: This table contains configuration template data that is for server configurations\.

- **FLW\_CTRL\_SERVER\_CONFIG**: This table contains the configuration data for accessing Flowable REST API endpoints\.

- **FLW\_CTRL\_TOKEN**: When a user successfully logs in, a token gets stored as part of the session-data if the IDM engine is not in use\.

- **FLW\_CTRL\_USER**: This table stores a specific instance of a user definition if the IDM engine is not in use\.

## Flowable Design Database Tables[​](#flowable-design-database-tables "Direct link to Flowable Design Database Tables")

The tables associated with Flowable Design start with **ACT\_DE\_**\.

- **ACT\_DE\_MODEL**: This table contains the metadata and the JSON representation of the most recent design model created\.

- **ACT\_DE\_MODEL\_HISTORY**: Each version of a design model is stored in this table\.

- **ACT\_DE\_MODEL\_RELATION**: This table contains the child models of a process or case model\.

- **ACT\_DE\_TOKEN**: When a user successfully logs in, a token gets stored as part of the session-data if the IDM engine is not in use\.

- **ACT\_DE\_USER**: This table stores a specific instance of a user definition if the IDM engine is not in use\.

## Flowable License Database Tables[​](#flowable-license-database-tables "Direct link to Flowable License Database Tables")

The license tables store a copy of the Flowable License file\. The tables have the prefix **FLW\_LIC\_**\.

## Database Scripts[​](#database-scripts "Direct link to Database Scripts")

By default the Flowable Work and Engage database tables are created when booting the application for the first time\. This means that the database user needs to have rights to create tables, primary keys, foreign keys, indexes etc\. When the application database user does not have those rights, it is possible to create the database tables with these [database scripts](https://developer-docs.flowable.com/db-scripts/2026.1/db-scripts-2026.1.zip)\. The ZIP file contains database SQL files for each supported database\. In the upgrade folder of the ZIP are the upgrade scripts when updating to a newer version of Flowable Work or Engage\. When manually creating or updating the database, ensure that you execute the *flowable\.engine\*\.sql* scripts **before** the *flowable\.work\*\.sql* \(for Flowable Work\) or *flowable\.engage\*\.sql* \(for Flowable Engage\) scripts\.

It is recommended to leave the database schema update property value to true, because that ensures that the database schema management framework \(Liquibase\) performs a verification to check if the tables are created by the SQL script files as expected\. It also ensures that the hash value that Liquibase uses has a correct value\.

flowable\.database-schema-update=true

It’s also possible to disable the Liquibase and database schema management logic in Flowable completely by setting the value to none\. In this case, Flowable will not do any checking if the tables are created as expected\.

flowable\.database-schema-update=ignore

Make sure that the value is not set to false, because in this case Liquibase will still do a version check if the tables are created as expected, but it will not be able to deal with hash value issues between environments, and this results in exceptions at startup\.

The property above is for Flowable Work / Engage / Orchestrate\. Flowable Design and Flowable Control have different properties\. Their properties are: `flowable.design.database-schema-update` and `flowable.control.app.database-schema-update` respectively\. The values and meaning of the values are the same as the ones for Work / Engage / Orchestrate\.