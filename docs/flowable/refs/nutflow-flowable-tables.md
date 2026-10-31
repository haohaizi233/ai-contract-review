# Flowable表结构 | Nutflow

TIP

- 本文档只介绍flowable中常用的数据库的表结构，即使你一个都不了解也不影响插件使用，其他想了解自行百度/谷歌。
- 要想对flowable数据库查询、操作可看[Service服务类](https://docs.nutflow.vip/flowable/service/repositoryService.html)。
- 插件无需对flowable数据库手动写SQL查询、操作，本文只作了解使用。（除非你想改flowable源码、数据结构、研究原理等当我没说）

## 命名规则 [​](#命名规则)

**ACT\_GE\_\***: 普通数据，各种情况都使用的数据。

**ACT\_HI\_\***: 'HI'表示 history。HistoryService 服务类操作的表。这些表包含着历史的相关数据，如结束的流程实例，变量，任务，等等。（在flowable中结束的流程只能查看，无法操作。若你想修改填写的表单数据请修改ACT\_HI\_VARINST表）

**ACT\_RE\_\***: 'RE'表示 repository（存储）。RepositoryService 服务类操作的表。带此前缀的表包含的是静态信息，如，流程定义，流程的资源（图片，规则等）。

**ACT\_RU\_\***: 'RU'表示 runtime。RuntimeService 服务类操作的表。运行时的表存储着流程变量，用户任务，变量，定时（job）等运行时的数据。flowable 只存储实例执行期间的运行时数据，当流程实例结束时，将删除这些记录。这就保证了这些运行时的表小且快。

## 表结构 [​](#表结构)

表分类表名表说明  
基础数据\(2\)ACT\_GE\_BYTEARRAY通用的流程定义和流程资源（部署后的xml、变量中的类型为对象的以字节形式存储）  
ACT\_GE\_PROPERTYflowable系统相关属性（flowable使用的数据库版本等基本信息）  
流程历史记录\(7\)ACT\_HI\_ACTINST历史流程节点实例  
ACT\_HI\_ATTACHMENT历史流程附件（评论中的附件存储在此）  
ACT\_HI\_COMMENT历史流程审批意见（评论中文字存储在此）  
ACT\_HI\_IDENTITYLINK历史流程运行过程中用户关系（候选人、候选组、参与者等信息）  
ACT\_HI\_PROCINST历史流程实例  
ACT\_HI\_TASKINST历史任务实例  
ACT\_HI\_VARINST历史流程中的变量信息（string、number类型可直接修改。若字段是对象或数组类型会存为BYTE，并存在ACT\_GE\_BYTEARRAY表中，替换的办法就是发起一个填写正确的流程，使用SQL进行替换。）  
流程定义表\(2\)ACT\_RE\_DEPLOYMENT流程模型部署  
ACT\_RE\_PROCDEF已部署的流程定义  
运行实例表\(8\)ACT\_RU\_ACTINST运行时流程节点实例  
ACT\_RU\_EXECUTION运行时流程执行实例  
ACT\_RU\_IDENTITYLINK运行时流程用户关系信息 （候选人、候选组、参与者等信息）  
ACT\_RU\_TASK运行时任务实例  
ACT\_RU\_VARIABLE运行时变量  
ACT\_RU\_JOB定时任务  
ACT\_RU\_SUSPENDED\_JOB定时任务 - 暂停  
ACT\_RU\_TIMER\_JOB定时任务 - 计时器