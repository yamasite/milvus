import logging
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy import and_, or_
from mishards.models import Tables, TableFiles
from mishards.router import RouterMixin
from mishards import exceptions, db
from mishards.hash_ring import HashRing

logger = logging.getLogger(__name__)


class Factory(RouterMixin):
    name = 'FileBasedHashRingRouter'

    def __init__(self, writable_topo, readonly_topo, **kwargs):
        super(Factory, self).__init__(writable_topo=writable_topo,
                                      readonly_topo=readonly_topo)

    def routing(self, table_name, partition_tags=None, metadata=None, **kwargs):
        range_array = kwargs.pop('range_array', None)
        return self._route(table_name, range_array, partition_tags, metadata, **kwargs)

    def _route(self, table_name, range_array, partition_tags=None, metadata=None, **kwargs):
        # PXU TODO: Implement Thread-local Context
        # PXU TODO: Session life mgt

        if not partition_tags:
            cond = and_(
                or_(Tables.table_id == table_name, Tables.owner_table == table_name),
                Tables.state != Tables.TO_DELETE)
        else:
            # TODO: collection default partition is '_default'
            cond = and_(Tables.state != Tables.TO_DELETE,
                        Tables.owner_table == table_name,
                        Tables.partition_tag.in_(partition_tags))
            if '_default' in partition_tags:
                default_par_cond = and_(Tables.table_id == table_name, Tables.state != Tables.TO_DELETE)
                cond = or_(cond, default_par_cond)
        try:
            tables = db.Session.query(Tables).filter(cond).all()
        except sqlalchemy_exc.SQLAlchemyError as e:
            raise exceptions.DBError(message=str(e), metadata=metadata)

        if not tables:
            logger.error("Cannot find table {} / {} in metadata".format(table_name, partition_tags))
            raise exceptions.TableNotFoundError('{}:{}'.format(table_name, partition_tags), metadata=metadata)

        table_list = [str(table.table_id) for table in tables]

        file_type_cond = or_(
            TableFiles.file_type == TableFiles.FILE_TYPE_RAW,
            TableFiles.file_type == TableFiles.FILE_TYPE_TO_INDEX,
            TableFiles.file_type == TableFiles.FILE_TYPE_INDEX,
        )
        file_cond = and_(file_type_cond, TableFiles.table_id.in_(table_list))
        try:
            files = db.Session.query(TableFiles).filter(file_cond).all()
        except sqlalchemy_exc.SQLAlchemyError as e:
            raise exceptions.DBError(message=str(e), metadata=metadata)

        if not files:
            logger.warning("Table file is empty. {}".format(table_list))
        #     logger.error("Cannot find table file id {} / {} in metadata".format(table_name, partition_tags))
        #     raise exceptions.TableNotFoundError('Table file id not found. {}:{}'.format(table_name, partition_tags),
        #                                         metadata=metadata)

        db.remove_session()

        servers = self.readonly_topo.group_names
        logger.info('Available servers: {}'.format(list(servers)))

        ring = HashRing(servers)

        routing = {}

        for f in files:
            target_host = ring.get_node(str(f.id))
            sub = routing.get(target_host, None)
            if not sub:
                sub = []
                routing[target_host] = sub
            routing[target_host].append(str(f.id))

        return routing

    @classmethod
    def Create(cls, **kwargs):
        writable_topo = kwargs.pop('writable_topo', None)
        if not writable_topo:
            raise RuntimeError('Cannot find \'writable_topo\' to initialize \'{}\''.format(self.name))
        readonly_topo = kwargs.pop('readonly_topo', None)
        if not readonly_topo:
            raise RuntimeError('Cannot find \'readonly_topo\' to initialize \'{}\''.format(self.name))
        router = cls(writable_topo=writable_topo, readonly_topo=readonly_topo, **kwargs)
        return router


def setup(app):
    logger.info('Plugin \'{}\' Installed In Package: {}'.format(__file__, app.plugin_package_name))
    app.on_plugin_setup(Factory)
